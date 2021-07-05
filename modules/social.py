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
import yaml
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
            115840403458097161,  # FlapSnapple
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

        with open("resources/profiles/themes.yml", 'r') as stream:
            self.themes = yaml.safe_load(stream)

        with open("resources/profiles/borders.yml", 'r') as stream:
            self.borders = yaml.safe_load(stream)

        with open("resources/profiles/backgrounds.yml", 'r') as stream:
            self.backgrounds = yaml.safe_load(stream)

            for bg_name in self.backgrounds.keys():
                self.backgrounds[bg_name]["image"] = self._render_background_image(bg_name)

        for theme in self.themes.keys():
            self.themes[theme]['pfpBackground'] = Image.open(
                f'resources/profiles/layout/{theme}/pfp-background.png'
            ).convert('RGBA')
            self.themes[theme]['missingImage'] = (
                Image.open(f'resources/profiles/layout/{theme}/missing-game.png').convert("RGBA").resize((45, 45))
            )
            self.themes[theme]['profileStatic'] = self._init_profile_static(theme)  # Do this last

        self.trophyImgCache = {}
        self.borderImgCache = {}
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

        self.special_trophies = {
            'bot': lambda member, guild: member.bot,
            'owner': lambda member, guild: member.id == guild.owner.id,
            'developer': lambda member, guild: member.id in self.bot_contributors,
            'chat-mod': lambda member, guild: guild.get_role(config.chatmod) in member.roles,
            'sub-mod': lambda member, guild: guild.get_role(config.submod) in member.roles,
            'mod-emeritus': lambda member, guild: guild.get_role(config.modemeritus) in member.roles
            or guild.get_role(config.submodemeritus) in member.roles,
            'helpful-user': lambda member, guild: guild.get_role(config.helpfulUser) in member.roles,
            'booster': lambda member, guild: guild.get_role(config.boostRole) in member.roles,
            'verified': lambda member, guild: guild.get_role(config.verified) in member.roles,
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
        return draw.text(xy, text, tuple(fill), font)

    def _init_profile_static(self, theme_name: str) -> Image:
        '''Inits static elements above background for profile card precache'''
        theme = self.themes[theme_name]

        fonts = self.profileFonts
        img = Image.new('RGBA', theme['pfpBackground'].size, (0, 0, 0, 0))

        snoo = Image.open('resources/profiles/layout/snoo.png').convert("RGBA")
        trophyUnderline = Image.open(f'resources/profiles/layout/{theme_name}/trophy-case-underline.png').convert(
            "RGBA"
        )
        gameUnderline = Image.open(f'resources/profiles/layout/{theme_name}/favorite-games-underline.png').convert(
            "RGBA"
        )

        img.paste(snoo, (50, 50), snoo)
        img.paste(trophyUnderline, (1150, 100), trophyUnderline)
        img.paste(gameUnderline, (60, 645), gameUnderline)

        draw = ImageDraw.Draw(img)
        self._draw_text(draw, (150, 50), '/r/NintendoSwitch Discord', theme['branding'], fonts['meta'])
        self._draw_text(draw, (150, 90), 'User Profile', theme['secondary_heading'], fonts['meta'])
        self._draw_text(draw, (60, 470), 'Member since', theme['secondary_heading'], fonts['small'])
        self._draw_text(draw, (435, 470), 'Messages sent', theme['secondary_heading'], fonts['small'])
        self._draw_text(draw, (790, 470), 'Local time', theme['secondary_heading'], fonts['small'])
        self._draw_text(draw, (60, 595), 'Favorite games', theme['primary_heading'], fonts['medium'])
        self._draw_text(draw, (1150, 45), 'Trophy case', theme['primary_heading'], fonts['medium'])

        return img

    def _render_background_image(self, name: str) -> Image:
        bg = self.backgrounds[name]

        img = Image.open(f'resources/profiles/backgrounds/{name}.png').convert("RGBA")

        trophy_bg_path = f'resources/profiles/layout/{bg["theme"]}/trophy-bg/{bg["trophy-bg-opacity"]}.png'
        trophy_bg = Image.open(trophy_bg_path).convert("RGBA")

        final = Image.alpha_composite(img, trophy_bg.resize(img.size))
        return final

    def _cache_trophy_image(self, name: str, theme_name: str) -> Image:
        if name is None:
            name = f'none-{theme_name}'
            path = f'resources/profiles/layout/{theme_name}/trophy-blank.png'
        else:
            path = 'resources/profiles/trophies/{}.png'.format(name)

        if not name in self.trophyImgCache:
            self.trophyImgCache[name] = Image.open(path).convert("RGBA")

        return self.trophyImgCache[name]

    def _cache_border_image(self, name: str) -> Image:
        if not name in self.borderImgCache:
            self.borderImgCache[name] = Image.open('resources/profiles/borders/{}.png'.format(name)).convert("RGBA")

        return self.borderImgCache[name]

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

    async def _cache_game_img(self, gamesDb, guid: str, theme) -> Image:
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
                return theme['missingImage']

            try:
                gameImg = await Games.get_image(guid, 'icon_url')

                if gameImg:
                    gameIcon = Image.open(gameImg).convert('RGBA').resize((45, 45))
                else:
                    gameIcon = None

            except Exception as e:
                logging.error('Error caching game icon', exc_info=e)
                gameIcon = None

            self.gameImgCache[guid] = (time.time() + 60 * 60 * 48, gameIcon)  # Expire in 48 hours

        if self.gameImgCache[guid][IMAGE] is None:
            return theme['missingImage']

        return self.gameImgCache[guid][IMAGE]

    async def _generate_profile_card(self, member: discord.Member) -> discord.File:
        db = mclient.bowser.users
        dbUser = db.find_one({'_id': member.id})

        if 'default' in dbUser['backgrounds']:
            backgrounds = list(dbUser['backgrounds'])
            backgrounds.remove('default')
            backgrounds.insert(0, 'default-dark')
            backgrounds.insert(0, 'default-light')

            db.update_one({'_id': member.id}, {'$set': {'backgrounds': backgrounds}})

            if dbUser['background'] == 'default':
                db.update_one({'_id': member.id}, {'$set': {'background': 'default-light'}})

            dbUser = db.find_one({'_id': member.id})

        background = self.backgrounds[dbUser['background']]
        theme = self.themes[background["theme"]]

        pfpBytes = io.BytesIO(await member.avatar_url_as(format='png', size=256).read())
        pfp = Image.open(pfpBytes).convert("RGBA").resize((250, 250))

        card = theme['pfpBackground'].copy()
        card.paste(pfp, (50, 170), pfp)
        card.paste(background["image"], mask=background["image"])
        card.paste(theme['profileStatic'], mask=theme['profileStatic'])

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
                    draw.text((nameW, 215), memberName, tuple(theme["primary"]), member_name_font)
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
            draw.text((nameW, 215), memberName, tuple(theme["primary"]), member_name_font)

        self._draw_text(draw, (350, 275), '#' + member.discriminator, theme["secondary"], fonts['subtext'])

        if dbUser['regionFlag']:
            regionImg = self._cache_flag_image(dbUser['regionFlag'])
            card.paste(regionImg, (976, 50), regionImg)

        # Friend code
        if dbUser['friendcode']:
            self._draw_text(draw, (350, 330), dbUser['friendcode'], theme["friend_code"], fonts['subtext'])

        # Start customized content -- stats
        message_count = f'{mclient.bowser.messages.find({"author": member.id}).count():,}'
        self._draw_text(draw, (435, 505), message_count, theme["primary"], fonts['medium'])

        joins = dbUser['joins']
        joins.sort()
        joinDate = datetime.datetime.utcfromtimestamp(joins[0])
        try:  # -d doesn't work on all platforms, such as Windows
            joinDateF = joinDate.strftime('%b. %-d, %Y')
        except:
            joinDateF = joinDate.strftime('%b. %d, %Y')
        self._draw_text(draw, (60, 505), joinDateF, theme["primary"], fonts['medium'])

        if not dbUser['timezone']:
            self._draw_text(draw, (790, 505), 'Not specified', theme["secondary"], fonts['medium'])

        else:
            tznow = datetime.datetime.now(pytz.timezone(dbUser['timezone']))
            localtime = tznow.strftime('%H:%M')
            tzOffset = tznow.strftime('%z')

            if tzOffset[-2:] == '00':  # Remove 00 at end, if present
                tzOffset = tzOffset[:-2]
            if tzOffset[1] == '0':  # Remove 0 at start of ±0X, if present
                tzOffset = tzOffset[0] + tzOffset[2:]

            self._draw_text(draw, (790, 505), f'{localtime} (UTC{tzOffset})', theme["primary"], fonts['medium'])

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

        for trophy, lambda_function in self.special_trophies.items():
            if lambda_function(member, guild):
                trophies.append(trophy)

        if len(trophies) < 15:  # Check for additional non-prefered trophies
            for x in dbUser['trophies']:
                if x not in trophies:
                    trophies.append(x)

        while len(trophies) < 15:
            trophies.append(None)

        trophyNum = 0
        useBorder = None
        for x in trophies:
            if useBorder is None and x in self.borders['trophy_borders']:
                useBorder = self.borders['trophy_borders'][x]

            trophyBadge = self._cache_trophy_image(x, background["theme"])
            card.paste(trophyBadge, trophyLocations[trophyNum], trophyBadge)
            trophyNum += 1

        # border!
        useBorder = useBorder or self.borders['default']
        border = self._cache_border_image(useBorder)
        card.paste(border, (0, 0), border)

        # Start favorite games
        setGames = dbUser['favgames']
        gameCount = 0
        Games = self.bot.get_cog('Games')
        if setGames:
            gameIconLocations = {0: (60, 665), 1: (60, 730), 2: (60, 795)}
            gameTextLocations = {0: 660, 1: 725, 2: 791}
            gamesDb = mclient.bowser.games

            setGames = list(dict.fromkeys(setGames))  # Remove duplicates from list, just in case
            setGames = setGames[:3]  # Limit to 3 results, just in case

            for game_guid in setGames:
                if not Games:
                    continue

                gameName = Games.get_preferred_name(game_guid)

                if not gameName:
                    continue

                gameIcon = await self._cache_game_img(gamesDb, game_guid, theme)
                card.paste(gameIcon, gameIconLocations[gameCount], gameIcon)

                nameW = 120
                nameWMax = 950

                game_name_font = fonts['medium'][self._determine_cjk_font(gameName)]

                for char in gameName:
                    if nameW >= nameWMax:
                        draw.text(
                            (nameW, gameTextLocations[gameCount]), '...', tuple(theme["primary"]), font=game_name_font
                        )
                        break

                    draw.text((nameW, gameTextLocations[gameCount]), char, tuple(theme["primary"]), font=game_name_font)
                    nameW += game_name_font.getsize(char)[0]
                gameCount += 1

        if gameCount == 0:  # No games rendered
            self._draw_text(draw, (60, 665), 'Not specified', theme["secondary_heading"], fonts['medium'])

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
                        continue

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
            await message.channel.send(phase5.format(', '.join(backgrounds)))
            while True:
                response = await self.bot.wait_for('message', timeout=120, check=check)

                content = response.content.lower().strip()
                if response.content.lower().strip() == 'reset':
                    db.update_one({'_id': ctx.author.id}, {'$set': {'background': 'default-light'}})
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

    @commands.has_any_role(config.moderator, config.eh)
    @_profile.command(name='grant')
    async def _profile_grant(self, ctx, item: str, member: discord.Member, name: str):
        '''Grants specified item, background or trophy, to a member'''
        item = item.lower()
        name = name.lower()

        if item not in ['background', 'trophy']:
            return await ctx.send(f'{config.redTick} Invalid item: {item}. Expected either `background` or `trophy`')

        if item == 'background' and name not in self.backgrounds:
            return await ctx.send(f'{config.redTick} Invalid background: {name}')

        if item == 'trophy':
            if not os.path.isfile(f'resources/profiles/trophies/{name}.png'):
                return await ctx.send(f'{config.redTick} Invalid trophy: {name}')

            if name in self.special_trophies:
                return await ctx.send(f'{config.redTick} Trophy cannot be granted via command: {name}')

        db = mclient.bowser.users
        dbUser = db.find_one({'_id': member.id})
        key = {'background': 'backgrounds', 'trophy': 'trophies'}[item]

        if name in dbUser[key]:
            return await ctx.send(f'{config.redTick} {member} already has {item} {name}')

        db.update_one({'_id': member.id}, {'$push': {key: name}})
        return await ctx.send(f'{config.greenTick} {item.title()} `{name}` granted to {member}')

    @commands.has_any_role(config.moderator, config.eh)
    @_profile.command(name='revoke')
    async def _profile_revoke(self, ctx, item: str, member: discord.Member, name: str):
        '''Revokes specified item, background or trophy, from a member'''
        item = item.lower()
        name = name.lower()

        if item not in ['background', 'trophy']:
            return await ctx.send(f'{config.redTick} Invalid item: {item}. Expected either `background` or `trophy`')

        if item == 'trophy' and name in self.special_trophies:
            return await ctx.send(f'{config.redTick} Trophy cannot be revoked via command: {name}')

        db = mclient.bowser.users
        dbUser = db.find_one({'_id': member.id})
        key = {'background': 'backgrounds', 'trophy': 'trophies'}[item]

        if name not in dbUser[key]:
            return await ctx.send(f'{config.redTick} {member} does not have {item} {name}')

        db.update_one({'_id': member.id}, {'$pull': {key: name}})
        return await ctx.send(f'{config.greenTick} {item.title()} `{name}` revoked from {member}')

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

    async def cog_command_error(self, ctx, error: commands.CommandError):
        if not ctx.command:
            return

        cmd_str = ctx.command.full_parent_name + ' ' + ctx.command.name if ctx.command.parent else ctx.command.name
        if isinstance(error, commands.MissingRequiredArgument):
            return await ctx.send(
                f'{config.redTick} Missing one or more required arguments. See `{ctx.prefix}help {cmd_str}`',
                delete_after=15,
            )

        elif isinstance(error, commands.BadArgument):
            return await ctx.send(
                f'{config.redTick} One or more provided arguments are invalid. See `{ctx.prefix}help {cmd_str}`',
                delete_after=15,
            )

        elif isinstance(error, commands.CheckFailure):
            return await ctx.send(f'{config.redTick} You do not have permission to run this command.', delete_after=15)

        else:
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
