import asyncio
import typing
import datetime
import time
import uuid
import logging
import re

import discord
import pymongo
import gridfs
import requests
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
	    'leaves': [],
        'lockdown': False,
        'jailed': False,
        'friendcode': None,
        'timezone': None,
        'trophies': [],
        'trophyPreference': [],
        'favgames': [],
        'regionFlag': None,
        'profileSetup': False,
        'background': 'default',
        'backgrounds': ['default']
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
    return docID

async def _request_noa(nsuid):
    infoDict = {}
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:74.0) Gecko/20100101 Firefox/74.0',
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Origin': 'https://www.nintendo.com',
        'X-Algolia-API-Key': '9a20c93440cf63cf1a7008d75f7438bf',
        'X-Algolia-Application-ID': 'U3B6GR4UA3',
        'Host': 'u3b6gr4ua3-dsn.algolia.net',
        'Referer': 'https://www.nintendo.com/pos-redirect/{}?a=gdp'.format(nsuid)
    }
    body = {
        "requests": [
            {
                "indexName": "noa_aem_game_en_us",
                "params": "query={}&hitsPerPage=1&maxValuesPerFacet=30&page=0".format(nsuid),
                "facetFilters": [["platform:Nintendo Switch"]]
            }
        ]
    }
    algolia = requests.post('https://u3b6gr4ua3-dsn.algolia.net/1/indexes/*/queries', headers=headers, json=body)
    try:
        algolia.raise_for_status()

    except Exception as e:
        raise RuntimeError(e)

    response = algolia.json()
    #print(response)
    if not response['results'][0]['hits']:
        raise KeyError('_noa game not found based on id')

    game = response['results'][0]['hits'][0]
    if not game:
        pass # TODO: fill in title for slug and web request

    description = discord.utils.escape_markdown(game['description']).replace(' \n      ', '').replace('    ', '').replace('\n\n', '\n')
    infoDict['description'] = re.sub(r'([,!?.:;])(?![\\n ])', '\g<1>', description)
    infoDict['category'] = ', '.join(game['categories'])
    infoDict['publisher'] = None if not game['publishers'] else ' & '.join(game['publishers'])
    infoDict['developer'] = None if not game['developers'] else ' & '.join(game['developers'])
    infoDict['image'] = 'https://nintendo.com' + game['boxArt']

    return infoDict

async def _request_noe(title):
    infoDict = {}
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:74.0) Gecko/20100101 Firefox/74.0'
    }

    search = requests.get('https://search.nintendo-europe.com/en/select?q="{}"&start=0&rows=4000&wt=json&sort=title asc&fq=type:GAME AND system_names_txt:"switch"'.format(title), headers=headers)
    try:
        search.raise_for_status()

    except Exception as e:
        raise RuntimeError(e)

    response = search.json()
    #print(response)
    gameList = response['response']['docs']
    game = None

    for entry in gameList:
        if entry['title'] == title:
            game = entry
            break

    if not game:
        raise KeyError('Game {} not found in returned NOE search array'.format(title))

    infoDict['description'] = None if not game['excerpt'] else game['excerpt']
    infoDict['category'] = ', '.join(game['pretty_game_categories_txt'])
    infoDict['publisher'] = None if not game['publisher'] else game['publisher']
    infoDict['developer'] = None if not game['developer'] else game['developer']
    infoDict['image'] = 'https:' + game['image_url']

    return infoDict


async def _request_noj(nsuid):
    infoDict = {}
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:74.0) Gecko/20100101 Firefox/74.0'
    }

    titleAPI = requests.get('https://ec.nintendo.com/api/JP/ja/related/title/{}'.format(nsuid), headers=headers)
    try:
        titleAPI.raise_for_status()

    except Exception as e:
        raise RuntimeError(e)

    gameDesc = titleAPI.json()
    #print(gameDesc)
    response = gameDesc['related_informations']['related_information'][0]

    infoDict['description'] = None if not response['description'] else response['description']
    infoDict['image'] = response['image_url']
    # TODO: Scrape nintendo.co.jp entries for extra details
    infoDict['category'] = None
    infoDict['publisher'] = None
    infoDict['developer'] = None

    return infoDict


async def game_data(nxid, desc_cap=2048):
    db = mclient.bowser.games
    fs = gridfs.GridFS(mclient.bowser)
    gameDoc = db.find_one({'_id': nxid})

    if gameDoc['description'] and gameDoc['cacheUpdate'] > (time.time() - 86400 * 30): # Younger than 30 days
        return gameDoc

    titles = gameDoc['titles']
    print(titles)

    if titles['NA']:
        infoDict = await _request_noa(gameDoc['nsuids']['NA'])

    elif titles['EU']:
        infoDict = await _request_noe(gameDoc['titles']['EU'].encode('latin-1', errors='replace'))

    else:
        infoDict = await _request_noj(gameDoc['nsuids']['JP'])

    if len(infoDict['description']) > desc_cap: infoDict['description'] = f'{infoDict["description"][:desc_cap - 3]}...'

    if not fs.exists(nxid) or gameDoc['cacheUpdate'] < (time.time() - 86400 * 30): # Image not stored or older than 30 days
        if fs.exists(nxid): fs.delete(nxid)
        r = requests.get(infoDict['image'], stream=True)
        if r.status_code != 200:
            raise RuntimeError(f'Nintendo returned non-200 status code {r.status_code}')

        fs.put(r.raw, _id=nxid)

    infoDict['size'] = None # Depreciated
    db.update_one({'_id': nxid}, {'$set': {
        'description': infoDict['description'],
        'category': infoDict['category'],
        'publisher': infoDict['publisher'],
        'developer': infoDict['developer'],
        'size': infoDict['size'],
        'image': infoDict['image'],
        'cacheUpdate': int(time.time())
        }})

    return infoDict

#async def scrape_nintendo(url, nxid, desc_cap=2048):
#    db = mclient.bowser.games
#    fs = gridfs.GridFS(mclient.bowser)
#    gameDoc = db.find_one({'_id': nxid})
#
#    if gameDoc['description'] and gameDoc['cacheUpdate'] > (time.time() - 86400 * 30): # Younger than 30 days
#        return gameDoc
#
#    logging.debug('[Utils] Starting chrome driver')
#    options = webdriver.ChromeOptions()
#    options.add_argument('--no-sandbox')
#    options.add_argument('--headless')
#    options.add_argument('--disable-dev-shm-usage')
#    options.add_argument('--disable-cache')
#    options.add_argument('--disable-extensions')
#    #options.add_argument('--user-data-dir=/dev/null')
#    driver = webdriver.Chrome('/root/mecha-bowser/python/bin/chromedriver', chrome_options=options)
#    logging.debug('[Utils] Chrome driver successfully started')
#
#    scrapedData = {}
#    while driver == None:
#        # Wait for the driver to start up if called before
#        logging.debug('[Deals] Waiting for chrome driver to start')
#        await asyncio.sleep(0.5)
#
#    driver.get(url)
#    await asyncio.sleep(2) # Because we are using chrome, we need to actually wait for the javascript redirect to run
#    soup = bs(driver.page_source, 'html.parser')
#
#    page = soup.find('div', attrs={'class': re.compile(r'(bullet-list drawer(?: truncated)?)')})
#    retrys = 0
#    while not page and retrys < 4: # Up to 10 seconds total wait time for the page to redirect
#        if driver.current_url == 'https://www.nintendo.com/games/':
#            raise KeyError('[Deals] scrape link redirected to main games site, dead link')
#
#        await asyncio.sleep(2)
#        page = soup.find('div', attrs={'class': re.compile(r'(bullet-list drawer(?: truncated)?)')})
#        retrys += 1
#        logging.warning(f'[Deals] Failed getting store page for {url}. Attempt {retrys + 1}')
#
#    if retrys >= 4:
#        logging.critical(f'[Deals] Failed to resolve data for store page {url}')
#        raise RuntimeError('Failed to resolve data for store page')
#
#    scrape = ''
#    for tag in page.children:
#        scrape += str(tag)
#
#    scrape = scrape.replace(u'\xa0', u' ') # Remove any weird latin space chars
#    scrape = scrape.strip() # Remove extra preceding/trailing whitespace
#    scrape = re.sub(r'(<[^>]*>)', '', scrape) # Remove HTML tags leaving text
#
#    scrapedData['description'] = discord.utils.escape_markdown(scrape).replace(' \n      ', '').replace('    ', '').replace('\n\n', '\n')
#    if len(scrapedData['description']) > desc_cap: scrapedData['description'] = f'{scrapedData["description"][:desc_cap - 3]}...'
#
#    imageScrape = soup.find('span', attrs={'class': 'boxart'})
#    if not imageScrape:
#        raise KeyError('boxart does not exist in HTML scrape')
#
#    for tag in imageScrape:
#        scrapeMinusDiv = str(tag)
#        imageTag = re.search(imageTagRe, scrapeMinusDiv)
#        if not imageTag: continue
#        if imageTag: break
#
#    nintendoRoot = re.search(storePageRe, driver.current_url).group(1)
#
#    scrapedData['image'] = nintendoRoot + imageTag.group(1)
#    scrapedData['category'] = None if not soup.find('div', attrs={'class': 'category'}) else soup.find('div', attrs={'class': 'category'}).dd.text.replace('\n', '').replace('  ', '')
#    scrapedData['publisher'] = None if not soup.find('div', attrs={'class': 'publisher'}) else soup.find('div', attrs={'class': 'publisher'}).dd.text.replace('\n', '').replace('  ', '')
#    scrapedData['developer'] = None if not soup.find('div', attrs={'class': 'developer'}) else soup.find('div', attrs={'class': 'developer'}).dd.text.replace('\n', '').replace('  ', '')
#    scrapedData['size'] = None if not soup.find('div', attrs={'class': 'file-size'}) else soup.find('div', attrs={'class': 'file-size'}).dd.text.replace('\n', '').replace('  ', '')
#
#    if not fs.exists(nxid) or gameDoc['cacheUpdate'] < (time.time() - 86400 * 30): # Image not stored or older than 30 days
#        if fs.exists(nxid): fs.delete(nxid)
#        r = requests.get(nintendoRoot + imageTag.group(1), stream=True)
#        if r.status_code != 200:
#            raise RuntimeError(f'Nintendo returned non-200 status code {r.status_code}')
#
#        fs.put(r.raw, _id=nxid)
#
#    db.update_one({'_id': nxid}, {'$set': {
#        'description': scrapedData['description'],
#        'category': scrapedData['category'],
#        'publisher': scrapedData['publisher'],
#        'developer': scrapedData['developer'],
#        'size': scrapedData['size'],
#        'image': scrapedData['image'],
#        'cacheUpdate': int(time.time())
#        }})
#
#    driver.quit()
#    return scrapedData

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
            if units[x] < -1 or units[x] > 1:
                expires.append('{} {}s'.format(units[x], unit_strs[x]))

            else:
                expires.append('{} {}'.format(units[x], unit_strs[x]))
    
    if not expires: return '0 seconds'
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
    if _type == 'ban':
        punDM += 'If you have questions concerning this matter, please feel free to contact the moderator that took this action.\n'

    else:
        punDM += f'If you have questions concerning this matter you may contact the moderation team by sending a DM to our modmail bot, Parakarry (<@{config.parakarry}>).\n'

    punDM += 'Please do not respond to this message, I cannot reply.'

    return punDM

def setup(bot):
    logging.info('[Extension] Utils module loaded')

def teardown(bot):
    logging.info('[Extension] Utils module unloaded')
