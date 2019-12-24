import asyncio
import typing
import datetime
import time
import uuid
import logging
import re

import discord
import pymongo
from selenium import webdriver
from bs4 import BeautifulSoup as bs

import config

driver = None
storePageRe = re.compile(r'(http[s]?:\/\/(?:[^.]*\.)?[^.]*\.[^\/]*)(?:.*)')
imageTagRe = re.compile(r'(?:src="([^"]*)")')
mclient = pymongo.MongoClient(
	config.mongoHost,
	username=config.mongoUser,
	password=config.mongoPass
)

archiveHeader = '# Message archive for guild "{0.name}" ({0.id})\nIncluded channels: {1}\n# Format:\n[date + time] Member ID/Message ID/Channel/Username - Message content\n----------------\n'
timeUnits = {
    's': lambda v: v,
    'm': lambda v: v * 60,
    'h': lambda v: v * 60 * 60,
    'd': lambda v: v * 60 * 60 * 24,
    'w': lambda v: v * 60 * 60 * 24 * 7,
}

async def message_archive(archive: typing.Union[discord.Message, list], edit=None):
    db = mclient.bowser.archive
    if type(archive) != list:
        # Single message to archive
        archive = [archive]

    channels = []
    for msg in archive:
        if f'#{msg.channel.name}' not in channels:
            channels.append(f'#{msg.channel.name}')

    body = archiveHeader.format(archive[0].guild, ', '.join(channels))
    archiveID = f'{archive[0].id}-{int(time.time() * 1000)}'
    messageIDs = []

    if edit:
        msgBefore = archive[0]
        msgAfter = archive[1]

        body += f'[{msgBefore.created_at.strftime("%Y/%m/%d %H:%M:%S UTC")}] ({msgBefore.author.id}/{msgBefore.id}/#{archive[0].channel.name}/{str(msgBefore.author)}): message edit:\n'
        body += f'--- Before ---\n{msgBefore.content}\n\n--- After ---\n{msgAfter.content}'

    else:
        for msg in archive: # TODO: attachment CDN urls should be posted as message
            messageIDs.append(msg.id)
            if not msg.content and msg.attachments:
                content = ' '.join([x.url for x in msg.attachments])

            elif not msg.content and not msg.attachments:
                content = '*No message content could be saved, could be embed*'

            else:
                content = msg.content

            body += f'[{msg.created_at.strftime("%Y/%m/%d %H:%M:%S UTC")}] ({msg.author.id}/{msg.id}/#{msg.channel.name}/{str(msg.author)}): {content}\n'

    db.insert_one({
        '_id': archiveID,
        'body': body,
        'messages': messageIDs,
        'timestamp': int(time.time())

    })
    return archiveID

async def store_user(member, messages=0):
    db = mclient.bowser.users
    # Double check exists
    if db.find_one({'_id': member.id}):
        logging.error('Attempted to store user that already exists!')
        return

    roleList = []
    for role in member.roles:
        if role.id == member.guild.id:
            continue
        
        roleList.append(role.id)

    userData = {
        '_id': member.id,
	    'roles': roleList,	
	    'joins': [(datetime.datetime.utcnow() - datetime.datetime.utcfromtimestamp(0)).total_seconds()],	
	    'leaves': []
    }
    db.insert_one(userData)

async def issue_pun(user, moderator, _type, reason=None, expiry=None, active=True, context=None, _date=None):
    db = mclient.bowser.puns
    timestamp = time.time() if not _date else _date
    docID = str(uuid.uuid4())
    while db.find_one({'_id': docID}): # Uh oh, duplicate uuid generated
        docID = str(uuid.uuid4())

    db.insert_one({
        '_id': docID,
        'user': user,
        'moderator': moderator,
        'type': _type,
        'timestamp': int(timestamp),
        'reason': reason,
        'expiry': expiry,
        'context': context,
        'active': active
    })

async def scrape_nintendo(url):
    print(url)
    scrapedData = {}
    while driver == None:
        # Wait for the driver to start up if called before
        await asyncio.sleep(0.5)

    driver.get(url)
    await asyncio.sleep(2) # Because we are using chrome, we need to actually wait for the javascript redirect to run
    soup = bs(driver.page_source, 'html.parser')

    page = soup.find('div', attrs={'class': re.compile(r'(bullet-list drawer(?: truncated)?)')})
    if not page:
        raise KeyError('bullet-list drawer does not exist in HTML scrape')

    scrape = ''
    for tag in page.children:
        scrape += str(tag)

    scrape = scrape.replace(u'\xa0', u' ') # Remove any weird latin space chars
    scrape = scrape.strip() # Remove extra preceding/trailing whitespace
    scrape = re.sub(r'(<[^>]*>)', '', scrape) # Remove HTML tags leaving text

    scrapedData['description'] = scrape

    imageScrape = soup.find('span', attrs={'class': 'boxart'})
    if not imageScrape:
        raise KeyError('boxart does not exist in HTML scrape')

    for tag in imageScrape:
        scrapeMinusDiv = str(tag)
        imageTag = re.search(imageTagRe, scrapeMinusDiv)
        if not imageTag: continue
        if imageTag: break

    gameRomSize = soup.find('dd', attrs={'itemprop': 'romSize'})
    if not gameRomSize:
        scrapedData['romSize'] = None

    else:
        scrapedData['romSize'] = gameRomSize.next_element.strip()

    #print('---data---')
    #print(page.find(string='Category'))
    #print(page.find(string='Category').next_element)
    #print(page.find(string='Category').next_element.next_element)
    #print('---end---')
    #scrapedData['category'] = re.search(r'([A-Z])\w+', str(page.find(string='Category').next_element.next_element)).group(0)
    with open('page.html', 'a') as page_html:
        page_html.write(str(soup.prettify()))
    scrapedData['manufacturer'] = soup.find('dd', attrs={'itemprop': 'manufacturer'})
    scrapedData['brand'] = soup.find('dd', attrs={'itemprop': 'brand'})

    nintendoRoot = re.search(storePageRe, driver.current_url).group(1)
    scrapedData['image'] = nintendoRoot + imageTag.group(1)

    print('image scrape date')
    print(scrapedData)
    return scrapedData

def resolve_duration(data):
    '''
    Takes a raw input string formatted 1w1d1h1m1s (any order)
    and converts to timedelta
    Credit https://github.com/b1naryth1ef/rowboat via MIT license

    data: str
    '''
    value = 0
    digits = ''

    for char in data:
        if char.isdigit():
            digits += char
            continue

        if char not in timeUnits or not digits:
            raise KeyError('Time format not a valid entry')

        value += timeUnits[char](int(digits))
        digits = ''

    return datetime.datetime.utcnow() + datetime.timedelta(seconds=value + 1)

def humanize_duration(duration):
    '''
    Takes a datetime object and returns a prettified
    weeks, days, hours, minutes, seconds string output
    Credit https://github.com/ThaTiemsz/jetski via MIT license

    duration: datetime.datetime
    '''
    now = datetime.datetime.utcnow()
    if isinstance(duration, datetime.timedelta):
        if duration.total_seconds() > 0:
            duration = datetime.datetime.today() + duration
        else:
            duration = datetime.datetime.utcnow() - datetime.timedelta(seconds=duration.total_seconds())
    diff_delta = duration - now
    diff = int(diff_delta.total_seconds())

    minutes, seconds = divmod(diff, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    weeks, days = divmod(days, 7)
    units = [weeks, days, hours, minutes, seconds]

    unit_strs = ['week', 'day', 'hour', 'minute', 'second']

    expires = []
    for x in range(0, 5):
        if units[x] == 0:
            continue
        else:
            if units[x] > 1:
                expires.append('{} {}s'.format(units[x], unit_strs[x]))
            else:
                expires.append('{} {}'.format(units[x], unit_strs[x]))
    
    return ', '.join(expires)

async def mod_cmd_invoke_delete(channel):
    if channel.id in config.showModCTX:
        return False

    else:
        return True

async def embed_paginate(chunks: list, page=1, header=None, codeblock=True):
    if page <= 0: raise IndexError('Requested page cannot be less than one')
    charLimit = 2048 if not codeblock else 2042 # 2048 - 6 for 6 backticks
    pages = 1
    requestedPage = ''

    if not header:
        text = ''

    else:
        text = header

    if codeblock:
        header = '```' if not header else header + '```'
        text = header

    for x in chunks:
        if len(x) > charLimit:
            raise IndexError('Individual chunk surpassed character limit')

        if len(text) + len(x) > charLimit:
            if pages == page:
                requestedPage = text if not codeblock else text + '```'

            text = header + x if header else x
            pages += 1
            continue

        text += x

    if page > pages:
        raise IndexError('Requested page out of range')

    if pages == 1:
        requestedPage = text if not codeblock else text + '```'

    return requestedPage, pages

def format_pundm(_type, reason, moderator, details=None, auto=False):
    infoStrs = {
        'warn': f'You have been **warned (now {details})** on',
        'warnup': f'Your **warning level** has been **increased (now {details})** on',
        'warndown': f'Your **warning level** has been **decreased (now {details})** on',
        'warnclear': f'Your **warning** has been **cleared** on',
        'mute': f'You have been **muted ({details})** on',
        'unmute': f'Your **mute** has been **removed** on',
        'blacklist': f'Your **posting permissions** have been **restricted** in {details} on',
        'unblacklist': f'Your **posting permissions** have been **restored** in {details} on',
        'kick': 'You have been **kicked** from',
        'ban': 'You have been **banned** from'
    }
    mod = f'{moderator} ({moderator.mention})' if not auto else 'Automatic action'

    punDM = infoStrs[_type] + f' the /r/NintendoSwitch Discord server.\n'
    punDM += f'Reason:```{reason}```'
    punDM += f'Responsible moderator: {mod}\n\n'
    punDM += 'If you have questions concerning this matter, please feel free to contact the respective moderator that took this action or another member of the moderation team.\n'
    punDM += 'Please do not respond to this message, I cannot reply.'

    return punDM

def setup(bot):
    global driver
    logging.info('[Utils] Starting chrome driver')
    options = webdriver.ChromeOptions()
    options.add_argument('--no-sandbox')
    options.add_argument('--headless')
    options.add_argument('--disable-dev-shm-usage')
    driver = webdriver.Chrome('/root/mecha-bowser/python/bin/chromedriver', chrome_options=options)
    logging.info('[Utils] Chrome driver successfully started')
    logging.info('[Extension] Utils module loaded')

def teardown(bot):
    driver.quit()
    logging.info('[Extension] Utils module unloaded')
