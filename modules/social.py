import asyncio
import datetime
import io
import logging
import os
import re
import time
import typing
from pathlib import Path

import codepoints
import config  # type: ignore
import discord
import emoji_data
import gridfs
import numpy as np
import pymongo
import pytz
import requests
import token_bucket
from discord.ext import commands
from fuzzywuzzy import process
from PIL import Image, ImageDraw, ImageFont

import tools  # type: ignore


mclient = pymongo.MongoClient(config.mongoHost, username=config.mongoUser, password=config.mongoPass)


class SocialFeatures(commands.Cog, name='Social Commands'):
    def __init__(self, bot):
        self.bot = bot
        self.inprogressEdits = {}

        # !profile ratelimits
        self.bucket_storage = token_bucket.MemoryStorage()
        self.profile_bucket = token_bucket.Limiter(1 / 30, 2, self.bucket_storage)  # burst limit 2, renews at 1 / 30 s

        # Profile generation
        self.twemojiPath = 'resources/twemoji/assets/72x72/'
        self.bot_contributors = [
            125233822760566784,  # MattBSG
            123879073972748290,  # Lyrus
            108429628560924672,  # Alex from Alaska
        ]

        # Profile generation - precaching
        self.profileFonts = self._load_fonts(
            {
                'meta': ('Regular', 36),
                'user': ('Regular', 48),
                'subtext': ('Light', 48),
                'medium': ('Light', 36),
                'small': ('Light', 30),
            }
        )
        self.pfpBackground = Image.open('resources/pfp-background.png').convert('RGBA')
        self.profileStatic = self._init_profile_static()
        self.missingImage = Image.open('resources/missing-game.png').convert("RGBA").resize((45, 45))
        self.fsImgCache = {}
        self.flagImgCache = {}
        self.gameImgCache = {}

        # Friend Code Regexs (\u2014 = em-dash)
        self.friendCodeRegex = {
            # Profile setup/editor (lenient)
            "profile": re.compile(r'(?:sw)?[ \-\u2014_]?(\d{4})[ \-\u2014_]?(\d{4})[ \-\u2014_]?(\d{4})', re.I),
            # Chat filter, "It appears you've sent a friend code." Requires separators and discards select prefixes.
            # Discarded prefixes: MA/MO (AC Designer), DA (AC Dream Address).
            "chatFilter": re.compile(
                r'(sw|m[^ao]|d[^a]|[^MD]\w|^\w|^)[ \-\u2014_]?\d{4}[ \-\u2014_]\d{4}[ \-\u2014_]\d{4}', re.I + re.M
            ),
        }

    @commands.group(name='profile', invoke_without_command=True)
    async def _profile(self, ctx, member: typing.Optional[discord.Member]):
        if not member:
            member = ctx.author

        # If channel can be ratelimited
        if ctx.message.channel.id not in [config.commandsChannel, config.voiceTextChannel, config.debugChannel]:
            channel_being_rate_limited = not self.profile_bucket.consume(str(ctx.channel.id))
            if channel_being_rate_limited:

                #  Moderators consume a ratelimit token but are not limited
                if not ctx.guild.get_role(config.moderator) in ctx.author.roles:
                    await ctx.send(
                        f'{config.redTick} That command is being used too often, try again in a few seconds.',
                        delete_after=15,
                    )
                    await ctx.message.delete(delay=15)
                    return

        db = mclient.bowser.users
        dbUser = db.find_one({'_id': member.id})

        # If profile not setup, running on self, not a mod, and not in commands channel: disallow running profile command
        if (
            not dbUser['profileSetup']
            and member == ctx.author
            and ctx.guild.get_role(config.moderator) not in ctx.author.roles
            and ctx.channel.id != config.commandsChannel
        ):
            await ctx.message.delete()
            return await ctx.send(
                f'{config.redTick} {ctx.author.mention} You need to setup your profile to view it outside of <#{config.commandsChannel}>! To setup your profile, use `!profile edit` in <#{config.commandsChannel}>.',
                delete_after=15,
            )

        card = await self._generate_profile_card(member)
        await ctx.send(file=card)

    def _load_fonts(self, fonts_defs):
        '''Load normal and CJK versions of given dict of fonts'''
        font_paths = {
            None: 'resources/notosans/NotoSans-{0}.ttf',
            'jp': 'resources/notosans/NotoSansCJKjp-{0}.otf',
        }
        fonts = {}

        for name, (weight, size) in fonts_defs.items():
            fonts[name] = {}
            for font, path in font_paths.items():
                font_path = path.format(weight)

                if not os.path.isfile(font_path):
                    raise Exception('Font file not found: ' + font_path)

                fonts[name][font] = ImageFont.truetype(font_path, size)

        return fonts

    # https://medium.com/the-artificial-impostor/4ac839ba313a
    def _determine_cjk_font(self, text):
        '''Determine correct CJK font, if needed'''
        if re.search("[\u3040-\u30ff\u4e00-\u9FFF]", text):
            return 'jp'
        return None

    def _draw_text(self, draw: ImageDraw, xy, text: str, fill, fonts: dict):
        font = fonts[self._determine_cjk_font(text)]
        return draw.text(xy, text, fill, font)

    def _init_profile_static(self) -> Image:
        '''Inits static elements above background for profile card precache'''
        fonts = self.profileFonts
        img = Image.new('RGBA', self.pfpBackground.size, (0, 0, 0, 0))

        snoo = Image.open('resources/snoo.png').convert("RGBA")
        trophyUnderline = Image.open('resources/trophy-case-underline.png').convert("RGBA")
        gameUnderline = Image.open('resources/favorite-games-underline.png').convert("RGBA")

        img.paste(snoo, (50, 50), snoo)
        img.paste(trophyUnderline, (1150, 100), trophyUnderline)
        img.paste(gameUnderline, (60, 645), gameUnderline)

        draw = ImageDraw.Draw(img)
        self._draw_text(draw, (150, 50), '/r/NintendoSwitch Discord', (45, 45, 45), fonts['meta'])
        self._draw_text(draw, (150, 90), 'User Profile', (126, 126, 126), fonts['meta'])
        self._draw_text(draw, (60, 470), 'Member since', (126, 126, 126), fonts['small'])
        self._draw_text(draw, (435, 470), 'Messages sent', (126, 126, 126), fonts['small'])
        self._draw_text(draw, (790, 470), 'Local time', (126, 126, 126), fonts['small'])
        self._draw_text(draw, (60, 595), 'Favorite games', (45, 45, 45), fonts['medium'])
        self._draw_text(draw, (1150, 45), 'Trophy case', (45, 45, 45), fonts['medium'])

        return img

    def _cache_fs_image(self, type: str, name: str) -> Image:
        if type == 'background':
            filename = 'resources/profile-{}.png'.format(name)
        elif type == 'trophy':
            filename = 'resources/trophies/{}.png'.format(name)
        else:
            raise ValueError('Unupported type: ' + type)

        if not filename in self.fsImgCache:
            self.fsImgCache[filename] = Image.open(filename).convert("RGBA")

        return self.fsImgCache[filename]

    def _cache_flag_image(self, name) -> Image:
        SHADOW_OFFSET = 2

        if not name in self.flagImgCache:

            regionImg = Image.open(self.twemojiPath + name + '.png').convert('RGBA')

            # Drop Shadow
            shadowData = np.array(regionImg)
            shadowData[..., :-1] = (128, 128, 128)  # Set RGB but not alpha for all pixels
            shadowImg = Image.fromarray(shadowData)

            # Combine shadow
            w, h = regionImg.size
            img = Image.new('RGBA', (w + SHADOW_OFFSET, h + SHADOW_OFFSET), (0, 0, 0, 0))
            img.paste(shadowImg, (SHADOW_OFFSET, SHADOW_OFFSET), shadowImg)
            img.paste(regionImg, (0, 0), regionImg)

            self.flagImgCache[name] = img

        return self.flagImgCache[name]

    def _cache_game_img(self, gamesDb, guid: str) -> Image:
        EXPIRY, IMAGE = 0, 1
        do_recache = False

        if guid in self.gameImgCache:
            if time.time() > self.gameImgCache[guid][EXPIRY]:  # Expired in cache
                do_recache = True
        else:  # Not in cache
            do_recache = True

        if do_recache:
            Games = self.bot.get_cog('Games')

            if not Games:
                return self.missingImage

            gameImgUrl = Games.get_image_url(guid, 'icon_url')

            if gameImgUrl:
                gameImg = requests.get(gameImgUrl, stream=True).raw
                gameIcon = Image.open(gameImg).convert('RGBA').resize((45, 45))
            else:
                gameIcon = None

            self.gameImgCache[guid] = (time.time() + 60 * 60 * 48, gameIcon)  # Expire in 48 hours

        if self.gameImgCache[guid][IMAGE] is None:
            return self.missingImage

        return self.gameImgCache[guid][IMAGE]

    async def _generate_profile_card(self, member: discord.Member) -> discord.File:
        db = mclient.bowser.users
        dbUser = db.find_one({'_id': member.id})

        pfpBytes = io.BytesIO(await member.avatar_url_as(format='png', size=256).read())
        pfp = Image.open(pfpBytes).convert("RGBA").resize((250, 250))
        background = self._cache_fs_image('background', dbUser['background'])

        card = self.pfpBackground.copy()
        card.paste(pfp, (50, 170), pfp)
        card.paste(background, mask=background)
        card.paste(self.profileStatic, mask=self.profileStatic)

        guild = member.guild
        draw = ImageDraw.Draw(card)
        fonts = self.profileFonts

        # userinfo
        memberName = ''
        nameW = 350

        # Member name may be rendered in parts, so we want to ensure the font stays the same for the entire thing
        member_name_font = fonts['user'][self._determine_cjk_font(member.name)]

        for char in member.name:
            if char not in emoji_data.EmojiSequence:
                memberName += char

            else:
                if memberName:
                    W, _ = draw.textsize(memberName, font=member_name_font)
                    draw.text((nameW, 215), memberName, (80, 80, 80), member_name_font)
                    nameW += W
                    memberName = ''

                charset = tuple(codepoints.from_unicode(char))
                unicodePoint = []
                for x in charset:
                    unicodePoint.append(hex(x)[2:])

                unicodeChar = '-'.join(unicodePoint)
                emojiPic = Image.open(self.twemojiPath + unicodeChar + '.png').convert('RGBA').resize((40, 40))
                card.paste(emojiPic, (nameW + 3, 228), emojiPic)
                nameW += 46

        if memberName:  # Leftovers, text
            draw.text((nameW, 215), memberName, (80, 80, 80), member_name_font)

        self._draw_text(draw, (350, 275), '#' + member.discriminator, (126, 126, 126), fonts['subtext'])

        if dbUser['regionFlag']:
            regionImg = self._cache_flag_image(dbUser['regionFlag'])
            card.paste(regionImg, (976, 50), regionImg)

        # Friend code
        if dbUser['friendcode']:
            self._draw_text(draw, (350, 330), dbUser['friendcode'], (87, 111, 251), fonts['subtext'])

        # Start customized content -- stats
        message_count = f'{mclient.bowser.messages.find({"author": member.id}).count():,}'
        self._draw_text(draw, (435, 505), message_count, (80, 80, 80), fonts['medium'])

        joins = dbUser['joins']
        joins.sort()
        joinDate = datetime.datetime.utcfromtimestamp(joins[0])
        try:  # -d doesn't work on all platforms, such as Windows
            joinDateF = joinDate.strftime('%b. %-d, %Y')
        except:
            joinDateF = joinDate.strftime('%b. %d, %Y')
        self._draw_text(draw, (60, 505), joinDateF, (80, 80, 80), fonts['medium'])

        if not dbUser['timezone']:
            self._draw_text(draw, (790, 505), 'Not specified', (126, 126, 126), fonts['medium'])

        else:
            tznow = datetime.datetime.now(pytz.timezone(dbUser['timezone']))
            localtime = tznow.strftime('%H:%M')
            tzOffset = tznow.strftime('%z')

            if tzOffset[-2:] == '00':  # Remove 00 at end, if present
                tzOffset = tzOffset[:-2]
            if tzOffset[1] == '0':  # Remove 0 at start of ±0X, if present
                tzOffset = tzOffset[0] + tzOffset[2:]

            self._draw_text(draw, (790, 505), f'{localtime} (UTC{tzOffset})', (80, 80, 80), fonts['medium'])

        # Start trophies
        trophyLocations = {
            0: (1150, 150),
            1: (1300, 150),
            2: (1450, 150),
            3: (1150, 300),
            4: (1300, 300),
            5: (1450, 300),
            6: (1150, 450),
            7: (1300, 450),
            8: (1450, 450),
            9: (1150, 600),
            10: (1300, 600),
            11: (1450, 600),
            12: (1150, 750),
            13: (1300, 750),
            14: (1450, 750),
        }
        trophies = []
        if dbUser['trophyPreference']:
            for x in dbUser:
                trophies.append(x)

        if member.id == guild.owner.id:  # Server owner
            trophies.append('owner')

        if member.id in self.bot_contributors:  # Developer
            trophies.append('developer')

        if guild.get_role(config.chatmod) in member.roles:  # Chat-mod role
            trophies.append('chat-mod')

        if guild.get_role(config.submod) in member.roles:  # Sub-mod role
            trophies.append('sub-mod')

        if (
            guild.get_role(config.modemeritus) in member.roles or guild.get_role(config.submodemeritus) in member.roles
        ):  # Mod emeritus
            trophies.append('mod-emeritus')

        if guild.get_role(config.helpfulUser) in member.roles:  # Helpful user
            trophies.append('helpful-user')

        if guild.get_role(config.boostRole) in member.roles:  # Booster role
            trophies.append('booster')

        if len(trophies) < 15:  # Check for additional non-prefered trophies
            for x in dbUser['trophies']:
                if x not in trophies:
                    trophies.append(x)

        while len(trophies) < 15:
            trophies.append('blank')

        trophyNum = 0
        for x in trophies:
            trophyBadge = self._cache_fs_image('trophy', x)
            card.paste(trophyBadge, trophyLocations[trophyNum], trophyBadge)
            trophyNum += 1

        # Start favorite games
        setGames = dbUser['favgames']
        gameCount = 0
        Games = self.bot.get_cog('Games')
        if setGames:
            gameIconLocations = {0: (60, 665), 1: (60, 730), 2: (60, 795)}
            gameTextLocations = {0: 660, 1: 725, 2: 791}
            gamesDb = mclient.bowser.games

            for game_guid in setGames:
                if not Games:
                    continue

                gameName = Games.get_preferred_name(game_guid)

                if not gameName:
                    continue

                gameIcon = self._cache_game_img(gamesDb, game_guid)
                card.paste(gameIcon, gameIconLocations[gameCount], gameIcon)

                nameW = 120
                nameWMax = 950

                game_name_font = fonts['medium'][self._determine_cjk_font(gameName)]

                for char in gameName:
                    if nameW >= nameWMax:
                        draw.text((nameW, gameTextLocations[gameCount]), '...', (80, 80, 80), font=game_name_font)
                        break

                    draw.text((nameW, gameTextLocations[gameCount]), char, (80, 80, 80), font=game_name_font)
                    nameW += game_name_font.getsize(char)[0]
                gameCount += 1

        if gameCount == 0:  # No games rendered
            self._draw_text(draw, (60, 665), 'Not specified', (126, 126, 126), fonts['medium'])

        bytesFile = io.BytesIO()
        card.save(bytesFile, format='PNG')
        return discord.File(io.BytesIO(bytesFile.getvalue()), filename='profile.png')

    def check_flag(self, emoji: str) -> typing.Optional[typing.Iterable[int]]:
        # For some reason emoji emoji_data.is_emoji_tag_sequence() does not return correctly, so we have to write our own function
        def is_valid_tag_flag(sequence: emoji_data.EmojiSequence) -> bool:
            BLACK_FLAG_EMOJI = u'\U0001F3F4'
            TAG_CHARACTERS = [chr(c) for c in range(ord('\U000E0020'), ord('\U000E007E') + 1)]
            TAG_TERMINATOR = u'\U000E007F'

            if seq.string[0] != BLACK_FLAG_EMOJI:  # First character
                return False
            if seq.string[-1] != TAG_TERMINATOR:  # Middle character
                return False
            for character in seq.string[1:-1]:  # Middle characters
                if character not in TAG_CHARACTERS:
                    return False

            return emoji_data.QualifiedType.FULLY_QUALIFIED

        # Locate EmojiSequence for given emoji
        for uni, seq in emoji_data.EmojiSequence:
            if uni == emoji:
                # Normal unicode flags
                is_emoji_flag = emoji_data.is_emoji_flag_sequence(emoji)
                # Flags such as england, scotland, and wales
                is_tag_flag = is_valid_tag_flag(seq)
                # Flags such as pirate, lgbt, and trans flag
                is_zwj_flag = emoji_data.is_emoji_zwj_sequence(emoji) and 'flag' in seq.description

                return seq.code_points if (is_emoji_flag or is_tag_flag or is_zwj_flag) else None

        # Emoji not found
        return None

    @_profile.command(name='edit')
    async def _profile_edit(self, ctx: commands.Context):
        db = mclient.bowser.users
        dbUser = db.find_one({'_id': ctx.author.id})
        mainMsg = None

        if (
            ctx.guild.get_role(config.moderator) not in ctx.author.roles and ctx.channel.id != config.commandsChannel
        ):  # commands
            await ctx.message.delete()
            return await ctx.send(
                f'{config.redTick} {ctx.author.mention} Please use bot commands in <#{config.commandsChannel}>, not {ctx.channel.mention}',
                delete_after=15,
            )

        if ctx.author.id in self.inprogressEdits.keys() and (time.time() - self.inprogressEdits[ctx.author.id]) < 300:
            await ctx.message.delete()
            return await ctx.send(
                f'{config.redTick} {ctx.author.mention} You are already editing your profile! Please finish or wait a few minutes before trying again',
                delete_after=15,
            )

        headerBase = 'Just a heads up! You can skip any section you do not want to edit right now by responding `skip` instead. Just edit your profile again to set it at a later time.'
        phase1 = 'What is your Nintendo Switch friend code? It looks like this: `SW-XXXX-XXXX-XXXX`'
        phase2 = 'What is the regional flag emoji for your country? Send a flag emoji like this: 🇺🇸'
        phase3 = 'What is your timezone region? You can find a list of regions here if you aren\'t sure: <http://www.timezoneconverter.com/cgi-bin/findzone.tzc>. For example, `America/New_York`'
        phase4 = 'Choose up to three (3) of your favorite games in total. You\'ve set {} out of 3 games so far. Send the title of a game as close to exact as possible, such as `1-2-Switch`'
        phase5 = 'Choose the background theme you would like to use for your profile. You have access to use the following themes: {}'

        # Lookup tables of values dependant on if user has setup their profile
        header = {
            True: f'{headerBase} If you would like to instead reset a section of your profile that you have previously set, just respond `reset` to any prompt.\n\n',
            False: f'{headerBase}\n\n',
        }

        embedText = {
            'title': {True: 'Edit your user profile', False: 'Setup your user profile'},
            'descBase': {
                True: 'Welcome back to profile setup.',
                False: 'It appears you have not setup your profile before, let\'s get that taken care of!',
            },
        }

        def check(m):
            return m.author.id == ctx.author.id and m.channel.id == mainMsg.channel.id

        async def _phase1(message):
            response = await self.bot.wait_for('message', timeout=120, check=check)

            content = response.content.lower().strip()
            if response.content.lower().strip() == 'skip':
                return True
            if response.content.lower().strip() == 'reset':
                db.update_one({'_id': ctx.author.id}, {'$set': {'friendcode': None}})
                await message.channel.send('I\'ve gone ahead and reset your setting for **friend code**')
                return True

            code = re.search(self.friendCodeRegex['profile'], content)
            if code:  # re match
                friendcode = f'SW-{code.group(1)}-{code.group(2)}-{code.group(3)}'
                db.update_one({'_id': ctx.author.id}, {'$set': {'friendcode': friendcode}})
                return True

            else:
                return False

        async def _phase2(message):
            response = await self.bot.wait_for('message', timeout=120, check=check)

            content = response.content.strip()
            if response.content.lower().strip() == 'skip':
                return True
            if response.content.lower().strip() == 'reset':
                db.update_one({'_id': ctx.author.id}, {'$set': {'regionFlag': None}})
                await message.channel.send('I\'ve gone ahead and reset your setting for **regional flag**')
                return True

            code_points = self.check_flag(content)
            if code_points is None:
                return False

            # Convert list of ints to lowercase hex code points, seperated by dashes
            pointStr = '-'.join('{:04x}'.format(n) for n in code_points)

            if not Path(f'{self.twemojiPath}{pointStr}.png').is_file():
                return False

            db.update_one({'_id': ctx.author.id}, {'$set': {'regionFlag': pointStr}})
            return True

        async def _phase3(message):
            response = await self.bot.wait_for('message', timeout=300, check=check)

            content = response.content.lower().strip()
            if response.content.lower().strip() == 'skip':
                return True
            if response.content.lower().strip() == 'reset':
                db.update_one({'_id': ctx.author.id}, {'$set': {'timezone': None}})
                await message.channel.send('I\'ve gone ahead and reset your setting for **timezone**')
                return True

            for x in pytz.all_timezones:
                if content == x.lower():
                    db.update_one({'_id': ctx.author.id}, {'$set': {'timezone': x}})
                    return True

            return False

        async def _phase4(message):
            failedFetch = False
            userGames = []
            Games = self.bot.get_cog('Games')

            if not Games:
                await message.channel.send(
                    'Err, oops! It looks like we can\'t reach the games system at this time! Skipping that for now...'
                )
                return True

            while len(userGames) < 3:
                if failedFetch:
                    await message.channel.send(
                        f'{config.redTick} Hmm, I can\'t add that game. Make sure you typed the game name correctly and don\'t add the same game twice.\n\n'
                        + phase4.format(len(userGames))
                    )
                else:
                    await message.channel.send(phase4.format(len(userGames)))
                    failedFetch = False

                response = await self.bot.wait_for('message', timeout=180, check=check)
                if response.content.lower().strip() == 'skip':
                    break

                if response.content.lower().strip() == 'reset':
                    db.update_one({'_id': ctx.author.id}, {'$set': {'favgames': []}})
                    await message.channel.send('I\'ve gone ahead and reset your setting for **favorite games**')
                    return True

                result = Games.search(response.content.strip(), True)

                if result:
                    if len(userGames) == 0 and dbUser['favgames']:
                        db.update_one({'_id': ctx.author.id}, {'$set': {'favgames': []}})

                    if result['guid'] in userGames:
                        failedFetch = True
                        break

                    name = Games.get_preferred_name(result['guid'])
                    msg = f'Is **{name}** the game you are looking for? Type __yes__ or __no__'

                    while True:
                        await message.channel.send(msg)

                        checkResp = await self.bot.wait_for('message', timeout=120, check=check)
                        if checkResp.content.lower().strip() in ['yes', 'y']:
                            db.update_one({'_id': ctx.author.id}, {'$push': {'favgames': result['guid']}})
                            userGames.append(result['guid'])
                            break

                        elif checkResp.content.lower().strip() in ['no', 'n']:
                            break

                        msg = "Your input was not __yes__ or __no__. Please say exactly __yes__ or __no__."

                else:
                    failedFetch = True

        async def _phase5(message):
            backgrounds = list(dbUser['backgrounds'])
            backgrounds.remove('default')

            if not backgrounds:
                await message.channel.send('Since you don\'t have any background themes unlocked we\'ll skip this step')
                return True

            else:
                backgrounds = list(dbUser['backgrounds'])
                await message.channel.send(phase5.format(', '.join(backgrounds)))
                while True:
                    response = await self.bot.wait_for('message', timeout=120, check=check)

                    content = response.content.lower().strip()
                    if response.content.lower().strip() == 'reset':
                        db.update_one({'_id': ctx.author.id}, {'$set': {'background': 'default'}})
                        await message.channel.send('I\'ve gone ahead and reset your setting for **profile background**')
                        return True

                    elif content != 'skip':
                        if content in backgrounds:
                            db.update_one({'_id': ctx.author.id}, {'$set': {'background': content}})
                            break

                        else:
                            await message.channel.send(
                                f'{config.redTick} That background name doesn\'t look right. Make sure to send one of the options given.\n\n'
                                + phase5.format(', '.join(backgrounds))
                            )

                    else:
                        break

        profileSetup = dbUser['profileSetup']

        embed = discord.Embed(
            title=embedText['title'][profileSetup],
            description=embedText["descBase"][profileSetup]
            + '\nYou can customize the following values:\n\n･ Your Nintendo Switch friend code\n･ The regional flag for your country'
            '\n･ Your timezone\n･ Up to three (3) of your favorite Nintendo Switch games\n･ The background theme of your profile'
            '\n\nWhen prompted, simply reply with what you would like to set the field as.',
        )
        embed.set_author(name=str(ctx.author), icon_url=ctx.author.avatar_url)

        try:
            mainMsg = await ctx.author.send(embed=embed)
            self.inprogressEdits[ctx.author.id] = time.time()
            await ctx.message.add_reaction('📬')
            private = True

        except discord.Forbidden:  # DMs not allowed, try in channel
            private = False
            return await ctx.send(
                f'{config.redTick} {ctx.author.mention} To edit your profile you\'ll need to open your DMs. I was unable to message you'
            )
            mainMsg = await ctx.send(ctx.author.mention, embed=embed)

        if not profileSetup:
            db.update_one({'_id': ctx.author.id}, {'$set': {'profileSetup': True}})

        botMsg = await mainMsg.channel.send(header[profileSetup] + phase1)
        try:
            # Phase 1
            phaseStart = time.time()
            phaseSuccess = False
            while not phaseSuccess:
                if not await _phase1(botMsg):
                    botMsg = await botMsg.channel.send(
                        f'{config.redTick} That friend code doesn\'t look right.\n\n' + phase1
                    )

                else:
                    phaseSuccess = True

            # Phase 2
            await botMsg.channel.send(phase2)

            phaseStart = time.time()
            phaseSuccess = False
            while not phaseSuccess:
                if not await _phase2(botMsg):
                    botMsg = await botMsg.channel.send(
                        f'{config.redTick} That emoji doesn\'t look right. Make sure you send only a flag emoji.\n\n'
                        + phase2
                    )

                else:
                    phaseSuccess = True

            # Phase 3
            await botMsg.channel.send(phase3)

            phaseStart = time.time()
            phaseSuccess = False
            while not phaseSuccess:
                if not await _phase3(botMsg):
                    botMsg = await botMsg.channel.send(
                        f'{config.redTick} That timezone doesn\'t look right. Make sure you send the timezone area exactly. If you are having trouble, ask a moderator for help or skip this part.\n\n'
                        + phase3
                    )

                else:
                    phaseSuccess = True

            phaseStart = time.time()
            phaseSuccess = False

            # Phase 4
            phaseStart = time.time()
            phaseSuccess = False
            await _phase4(botMsg)

            # Phase 5
            phaseStart = time.time()
            phaseSuccess = False
            await _phase5(botMsg)

            del self.inprogressEdits[ctx.author.id]
            card = await self._generate_profile_card(ctx.author)
            return await mainMsg.channel.send('You are all set! Your profile has been edited:', file=card)

        except asyncio.TimeoutError:
            await mainMsg.delete()
            del self.inprogressEdits[ctx.author.id]
            return await botMsg.edit(
                content=f'{ctx.author.mention} You have taken too long to respond and the edit has been timed out, please run `!profile edit` to start again'
            )

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.channel.type != discord.ChannelType.text or message.author.bot:
            return

        content = re.sub(r'(<@!?\d+>)', '', message.content)
        contains_code = tools.re_match_nonlink(self.friendCodeRegex['chatFilter'], content)

        if not contains_code:
            return
        if message.channel.id not in [config.commandsChannel, config.voiceTextChannel]:
            await message.channel.send(
                f'{message.author.mention} Hi! It appears you\'ve sent a **friend code**. An easy way to store and share your friend code is with our server profile system. To view your profile use the `!profile` command. To set details such as your friend code on your profile, use `!profile edit` in <#{config.commandsChannel}>. You can even see the profiles of other users with `!profile @user`'
            )

    async def cog_command_error(self, ctx, error):
        cmd_str = ctx.command.full_parent_name + ' ' + ctx.command.name if ctx.command.parent else ctx.command.name

        await ctx.send(
            f'{config.redTick} An unknown exception has occured, if this continues to happen contact the developer.',
            delete_after=15,
        )
        raise error


def setup(bot):
    bot.add_cog(SocialFeatures(bot))
    logging.info('[Extension] Social module loaded')


def teardown(bot):
    bot.remove_cog('SocialFeatures')
    logging.info('[Extension] Social module unloaded')
