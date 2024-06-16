import asyncio
import glob
import io
import logging
import math
import os
import random
import re
import time
import typing
from datetime import datetime, timezone
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
from discord import app_commands
from discord.ext import commands
from fuzzywuzzy import process
from PIL import Image, ImageDraw, ImageFont

import tools  # type: ignore


mclient = pymongo.MongoClient(config.mongoURI)


class SocialFeatures(commands.Cog, name='Social Commands'):
    def __init__(self, bot):
        self.bot = bot
        self.inprogressEdits = {}
        self.validate_allowed_users = []

        self.Games = self.bot.get_cog('Games')

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
                self.backgrounds[bg_name]["image"] = self._render_background_image_from_slug(bg_name)

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
            # Even more lenient FC for autocomplete
            "autocomplete": re.compile(r'(?:sw)?[ \-\u2014_]?(\d{1,4})[ \-\u2014_]?(\d{0,4})[ \-\u2014_]?(\d{0,4})', re.I),
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

        self.easter_egg_games = {
            self.bot.user.id: ["3030-80463"],  # Super Mario 3D All-Stars
            config.parakarry: ["3030-89844"],  # Paper Mario: The Thousand-Year Door
        }

        self.easter_egg_text = [  # Message text for bot easter egg, keep under 12 chars
            'A lot',
            'Enough',
            'Over 9000!',
            'idk',
            'Tree Fiddy',
            'Around 4',
            'Infinity',
            'Yes',
            'No',
            'Not specified',
            '???',
            'Reply hazy',
            'Most likely',
        ]

        self.INDEX, self.EMOTES = (0, 1)
        self.triviaTrophyData = [
            ('no-active-trophies', ''),
            ('trivia-bronze-1', '<:triviabronze1:1194031669498351656>'),
            ('trivia-bronze-2', '<:triviabronze2:1194031670421110945>'),
            ('trivia-bronze-3', '<:triviabronze3:1194031672690229338>'),
            ('trivia-silver-1', '<:triviasilver1:1194031683251482696>'),
            ('trivia-silver-2', '<:triviasilver2:1194031687810699366>'),
            ('trivia-silver-3', '<:triviasilver3:1194031688792154234>'),
            ('trivia-gold-1', '<:triviagold1:1194031674053382216>'),
            ('trivia-gold-2', '<:triviagold2:1194031676649652305>'),
            ('trivia-gold-3', '<:triviagold3:1194031677715005490>'),
        ]

        # Compile the most common timezones at runtime for autocomplete use
        db = mclient.bowser.users
        usersWithTimezones = db.find({'timezone': {'$ne': None}})
        timezones = {}
        for user in usersWithTimezones:
            if user['timezone'] not in timezones.keys():
                timezones[user['timezone']] = 1

            else:
                timezones[user['timezone']] += 1

        self.commonTimezones = [x[0] for x in sorted(timezones.items(), key=lambda tz: tz[1], reverse=True)]

    @app_commands.guilds(discord.Object(id=config.nintendoswitch))
    class SocialCommand(app_commands.Group):
        pass

    social_group = SocialCommand(
        name='profile', description='View and change your unique server profile to make it your own'
    )

    @social_group.command(name='view', description='Pull up and view your own server profile or someone elses!')
    @app_commands.describe(member='Who\'s profile you want to view. You can leave this blank to see your own')
    async def _profile(self, interaction: discord.Interaction, member: typing.Optional[discord.Member]):
        if not member:
            member = interaction.user

        # If channel can be ratelimited
        if interaction.channel.id not in [config.commandsChannel, config.debugChannel]:
            channel_being_rate_limited = not self.profile_bucket.consume(str(interaction.channel.id))
            if channel_being_rate_limited:
                #  Moderators consume a ratelimit token but are not limited
                if not interaction.guild.get_role(config.moderator) in interaction.user.roles:
                    await interaction.response.send_message(
                        f'{config.redTick} That command is being used too often, try again in a few seconds.',
                        ephemeral=True,
                    )
                    return

        db = mclient.bowser.users
        dbUser = db.find_one({'_id': member.id})

        # If profile not setup and running on self: force ephemeral and provide NUX
        if not dbUser['profileSetup'] and member == interaction.user:
            await interaction.response.defer(ephemeral=True)
            embed, card = await self.generate_user_flow_embed(member, new_user=True)
            return await interaction.followup.send(
                '👋 Hi there! It looks like you have not setup your profile card'
                ' quite yet. You won\'t be able to publicly post your card on your own until you have updated at '
                'least one element. This won\'t prevent other users from viewing your card if they request it however. '
                'Here are some helpful instructions for you to get started -- it\'s easy!',
                file=card,
                embed=embed,
            )

        else:
            await interaction.response.defer()
            card = await self._generate_profile_card_from_member(member)
            await interaction.followup.send(file=card)

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
        if re.search(r"[\u3040-\u30ff\u4e00-\u9FFF]", text):
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

    def _render_background_image_from_slug(self, name: str) -> Image:
        bg = self.backgrounds[name]
        img = Image.open(f'resources/profiles/backgrounds/{name}.png').convert("RGBA")
        return self._render_background_image(img, bg['theme'], bg['trophy-bg-opacity'])

    def _render_background_image(self, img, theme, trophy_bg_opacity):
        tbg_opacity = str(trophy_bg_opacity)

        ## Check theme ##
        valid_themes = next(os.walk('resources/profiles/layout/'))[1]

        if theme not in valid_themes:
            raise ValueError(f'Invalid theme {theme}, must be one of: {", ".join(valid_themes)}')

        ## Check opacity ##
        tcp = f'resources/profiles/layout/{theme}/trophy-bg/'
        valid_opac = [os.path.splitext(u)[0] for u in [t.split('/')[-1] for t in glob.glob(os.path.join(tcp, '*.png'))]]

        if tbg_opacity not in valid_opac:
            v = ", ".join(valid_opac)
            raise ValueError(f'Invalid trophy background opacity {tbg_opacity} for theme {theme}, must be one of: {v}')

        ## Render ##
        trophy_bg_path = f'resources/profiles/layout/{theme}/trophy-bg/{tbg_opacity}.png'
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
            if not self.Games:
                return theme['missingImage']

            try:
                gameImg = await self.Games.get_image(guid, 'icon_url')

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

    def _generate_background_preview(self, backgrounds) -> discord.File:
        # square_length: Gets smallest square dimensions that will fit length of backgrounds, ie len 17 -> 25
        square_length = math.ceil(math.sqrt(len(backgrounds)))

        # rows_required is used to chop the bottom off, i.e. 2 bgs have a 2x2 w/ square_length but we only need 2x1
        rows_required = math.ceil(len(backgrounds) / square_length)

        canvas = Image.new('RGBA', (1600 * square_length, 900 * rows_required), (0, 0, 0, 0))

        for i, name in enumerate(backgrounds):
            background = self.backgrounds[name]
            theme = self.themes[background["theme"]]

            image = theme['pfpBackground'].copy()
            draw = ImageDraw.Draw(image)

            image.paste(background["image"], mask=background["image"])
            image.paste(theme['profileStatic'], mask=theme['profileStatic'])
            self._draw_text(draw, (350, 215), name, theme["primary"], self.profileFonts['user'])

            paste_at = (i % square_length * 1600, i // square_length * 900)
            canvas.paste(image, paste_at, image)

        new_height = round((rows_required / square_length) * 900)
        canvas = canvas.resize((1600, new_height))

        bytesFile = io.BytesIO()
        canvas.save(bytesFile, format='PNG')
        return discord.File(io.BytesIO(bytesFile.getvalue()), filename='preview.png')

    async def _generate_profile_card_from_member(self, member: discord.Member) -> discord.File:
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

        ## Get avatar ##
        pfpBytes = io.BytesIO(await member.display_avatar.with_format('png').with_size(256).read())

        ## Get message count, games ##
        if member.id in self.easter_egg_games:
            setGames = self.easter_egg_games[member.id]
            message_count = random.choice(self.easter_egg_text)
        else:
            setGames = dbUser['favgames']
            setGames = list(dict.fromkeys(setGames))  # Remove duplicates from list, just in case
            setGames = setGames[:3]  # Limit to 3 results, just in case

            message_count = f'{mclient.bowser.messages.find({"author": member.id}).count():,}'

        ## Get join date ##
        joins = dbUser['joins']
        joins.sort()
        joinDate = datetime.fromtimestamp(joins[0], tz=timezone.utc)
        try:  # -d doesn't work on all platforms, such as Windows
            joinDateF = joinDate.strftime('%b. %-d, %Y')
        except:
            joinDateF = joinDate.strftime('%b. %d, %Y')

        ## Get current time ##
        if not dbUser['timezone']:
            usertime = 'Not specified'

        else:
            tznow = datetime.now(pytz.timezone(dbUser['timezone']))
            localtime = tznow.strftime('%H:%M')
            tzOffset = tznow.strftime('%z')

            if tzOffset[-2:] == '00':  # Remove 00 at end, if present
                tzOffset = tzOffset[:-2]
            if tzOffset[1] == '0':  # Remove 0 at start of ±0X, if present
                tzOffset = tzOffset[0] + tzOffset[2:]

            usertime = f'{localtime} (UTC{tzOffset})'

        ## Get Trophies ##
        trophies = []
        if dbUser['trophyPreference']:
            for x in dbUser:
                trophies.append(x)

        for trophy, lambda_function in self.special_trophies.items():
            if lambda_function(member, member.guild):
                trophies.append(trophy)

        if len(trophies) < 15:  # Check for additional non-prefered trophies
            for x in dbUser['trophies']:
                if x not in trophies:
                    trophies.append(x)

        while len(trophies) < 15:
            trophies.append(None)

        profile = {
            'pfp': Image.open(pfpBytes),
            'display_name': member.display_name,
            'username': str(member),
            'regionFlag': dbUser['regionFlag'],
            'friendcode': dbUser['friendcode'],
            'message_count': message_count,
            'joindate': joinDateF,
            'usertime': usertime,
            'trophies': trophies,
            'games': setGames,
        }

        return await self._generate_profile_card(profile, self.backgrounds[dbUser['background']])

    async def _generate_profile_card(self, profile: dict, background: dict) -> discord.File:
        theme = self.themes[background["theme"]]

        pfp = profile['pfp'].convert("RGBA").resize((250, 250))

        card = theme['pfpBackground'].copy()
        card.paste(pfp, (50, 170), pfp)
        card.paste(background["image"], mask=background["image"])
        card.paste(theme['profileStatic'], mask=theme['profileStatic'])

        draw = ImageDraw.Draw(card)
        fonts = self.profileFonts

        # userinfo
        memberName = ''
        nameW = 350

        # Member name may be rendered in parts, so we want to ensure the font stays the same for the entire thing
        member_name_font = fonts['user'][self._determine_cjk_font(profile['display_name'])]

        for char in profile['display_name']:
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

        self._draw_text(draw, (350, 275), profile['username'], theme["secondary"], fonts['subtext'])

        if profile['regionFlag']:
            regionImg = self._cache_flag_image(profile['regionFlag'])
            card.paste(regionImg, (976, 50), regionImg)

        # Friend code
        if profile['friendcode']:
            self._draw_text(draw, (350, 330), profile['friendcode'], theme["friend_code"], fonts['subtext'])

        self._draw_text(draw, (435, 505), profile['message_count'], theme["primary"], fonts['medium'])
        self._draw_text(draw, (60, 505), profile['joindate'], theme["primary"], fonts['medium'])
        self._draw_text(draw, (790, 505), profile['usertime'], theme["primary"], fonts['medium'])

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
        trophyNum = 0
        useBorder = None
        for x in profile['trophies']:
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
        gameIconLocations = {0: (60, 665), 1: (60, 730), 2: (60, 795)}
        gameTextLocations = {0: 660, 1: 725, 2: 791}

        setGames = profile['games']
        gameCount = 0
        if setGames:
            gamesDb = mclient.bowser.games

            setGames = list(dict.fromkeys(setGames))  # Remove duplicates from list, just in case
            setGames = setGames[:3]  # Limit to 3 results, just in case

            for game_guid in setGames:
                if not self.Games:
                    continue

                gameName = self.Games.get_preferred_name(game_guid)

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

    async def modify_trivia_level(self, member: discord.Member, regress=False):
        db = mclient.bowser.users
        dbUser = db.find_one({'_id': member.id})
        currentLevel = 0

        for t in dbUser['trophies']:
            if t.startswith('trivia-'):
                currentLevel = [n for (n, _) in self.triviaTrophyData].index(t)
                break

        newLevel = currentLevel - 1 if regress else currentLevel + 1
        if newLevel < 0 or newLevel > (len(self.triviaTrophyData) - 1):
            # Subtract 1 from the length as we have a 0 index value that doesn't contribute
            raise IndexError(f'New trivia level is out of range: {currentLevel} attempting to update to {newLevel}')

        if currentLevel > 0 and newLevel != 0:
            await tools.commit_profile_change(
                self.bot, member, 'trophy', self.triviaTrophyData[currentLevel][self.INDEX], revoke=True, silent=True
            )

        elif newLevel == 0:
            await tools.commit_profile_change(
                self.bot, member, 'trophy', self.triviaTrophyData[currentLevel][self.INDEX], revoke=True
            )

        if newLevel > 0:
            await tools.commit_profile_change(self.bot, member, 'trophy', self.triviaTrophyData[newLevel][self.INDEX])

        return newLevel

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

    async def generate_user_flow_embed(self, member: discord.Member, new_user: bool = False):
        '''
        Generates a discord.Embed with information profile card editing flow and all subcommands.

        returns discord.Embed, discord.File
        '''

        embed = discord.Embed(title='Setup Your Profile Card!', color=0x8BC062)
        embed.set_author(name=member.name, icon_url=member.display_avatar.url)
        embed.set_footer(text='❔You can see this info again anytime if you run the /profile edit command')

        main_img = await self._generate_profile_card_from_member(member)
        embed.set_image(url='attachment://profile.png')

        for command in self.bot.tree.get_commands(guild=discord.Object(id=config.nintendoswitch)):
            # Iterate over commands in the tree so we can get the profile command ID
            if command.name == 'profile':
                break

        commandID = command.extras['id']
        if new_user:
            # We need to minorly modify description for info for first time user flow
            embed_description = (
                '**Profile card not setup yet?**\nLet\'s fix that! It can show off your fav games,'
                f' a flag to represent you, & more. Use </profile view:{commandID}> to see anyone\'s profile card or your own. Customize it with the commands below!'
            )

        else:
            embed_description = (
                '**Looking to spice up your profile card?**\n It\'s easy to update and make it '
                f'your own. As a refresher, you can use </profile view:{commandID}> anytime to view anyone\'s profile card or your own. You can customize yours using the commands below!'
            )

        embed_description += (
            f'\n\n- **Add Your Friend Code**: </profile friendcode:{commandID}> Add your friend code to allow friend requests!'
            f'\n- **Pick a Timezone**: </profile timezone:{commandID}> Let others know what time it is for you and your timezone.'
            f'\n- **Rep a Flag**: </profile flag:{commandID}> Show your country 🇺🇳, be a pirate 🏴‍☠️, or rep pride 🏳️‍🌈 with flag emoji on your card!'
            f'\n- **Show Off Your Fav Games**: </profile games:{commandID}> Show off up-to 3 of your Switch game faves.'
            f'\n- **Choose a Different Background**: </profile background:{commandID}> Start with a light or dark theme. '
            'Earn more in events (like Trivia) to make your card pop!'
            '\n**Get Some Trophies**\nEarn a trophy when you participate in server events and Trivia!\n'
            'They\'ll show up automatically on your card when assigned by a moderator\n\n'
            'Default profiles are boring! Spruce it up!\n__Here\'s how your card currently looks:__'
        )
        embed.description = embed_description

        return embed, main_img  # Both need to be passed into a message for image embedding to function

    async def _profile_friendcode_autocomplete(
            self, interaction: discord.Interaction, current: str
    ) -> typing.List[app_commands.Choice[str]]:
        partialCode = re.search(self.friendCodeRegex['autocomplete'], current)

        removal = app_commands.Choice(name='Remove your friend code', value='remove')

        def pad_extra_chars(partial_code: str):
            length = len(partial_code)
            if length < 4:
                partial_code += '#' * (4 - length)

            return partial_code

        # Build a result
        friendcode = 'SW-'
        if not partialCode:
            # No match at all, return a default value
            return [
                app_commands.Choice(name='SW-####-####-####', value='SW-####-####-####'),
                removal
            ]

        friendcode += pad_extra_chars(partialCode.group(1))

        if partialCode.group(2):
            friendcode += '-' + pad_extra_chars(partialCode.group(2))
            if partialCode.group(3):
                friendcode += '-' + pad_extra_chars(partialCode.group(3))

            else:
                friendcode += '-####'


        else:
            friendcode += '-####-####'


        return [
            app_commands.Choice(name=friendcode, value=friendcode),
            removal
        ]

    @social_group.command(name='friendcode', description='Use this command to edit the display friend code on your profile')
    @app_commands.describe(code='Update your Switch Friend code, formatted as SW-0000-0000-0000. Type "remove" to remove it')
    @app_commands.autocomplete(code=_profile_friendcode_autocomplete)
    async def _profile_friendcode(self, interaction: discord.Interaction, code: str):
        await interaction.response.defer(ephemeral=True)
        db = mclient.bowser.users

        friendcode = re.search(self.friendCodeRegex['profile'], code)
        if friendcode:  # re match
            friendcode = f'SW-{friendcode.group(1)}-{friendcode.group(2)}-{friendcode.group(3)}'
            if friendcode == 'SW-0000-0000-0000':
                return await interaction.followup.send(f'{config.redTick} The Nintendo Switch friend code you provided is invalid, please try again. The format of a friend code is `SW-0000-0000-0000`, with the zeros replaced with the numbers from your unique code')

            db.update_one(
                {'_id': interaction.user.id},
                {'$set': {'friendcode': friendcode, 'profileSetup': True}}
            )

            msg = f'{config.greenTick} Your friend code has been successfully updated on your profile card! Here\'s how it looks:'

            # Duplicate friend code detection
            if db.count_documents({'friendcode': friendcode}) > 1:
                duplicates = db.find(
                    {'$and': {
                        {'_id': {'$ne': interaction.user.id}},
                        {'friendcode': friendcode}
                    }}
                )

                if duplicates:
                    # Check if accounts with matching friend codes have infractions on file
                    punsDB = mclient.bowser.puns
                    hasPuns = False
                    otherUsers = []
                    for u in duplicates:
                        if punsDB.count_documents({'user': u['_id']}):
                            hasPuns = True

                        if interaction.user.id != u['id']:
                            user = interaction.guild.get_member(u['_id'])
                            if not user:
                                user = await self.bot.fetch_user(u['_id'])

                            otherUsers.append(f'> **{user}** ({u["_id"]})')

                    if hasPuns:
                        admin_channel = self.bot.get_channel(config.nintendoswitch)
                        others = '\n'.join(otherUsers)
                        plural = "that of another user" if (len(otherUsers) == 1) else "those of other users"
                        await admin_channel.send(
                            f'🕵️ **{interaction.user}** ({interaction.user.id}) has set a friend code (`{friendcode}`) that matches {plural}: \n{others}'
                        )

        elif code.lower() == 'remove':
            db.update_one({'_id': interaction.user.id}, {'$set': {'friendcode': None}})
            msg = f'{config.greenTick} Your friend code has been successfully removed from your profile card! Here\'s how it looks:'

        else:
            return await interaction.followup.send(f'{config.redTick} The Nintendo Switch friend code you provided is invalid, please try again. The format of a friend code is `SW-0000-0000-0000`, with the zeros replaced with the numbers from your unique code')

        await interaction.followup.send(msg, file=await self._generate_profile_card_from_member(interaction.user))

    @social_group.command(name='flag', description='Choose an emoji flag to display on your profile')
    @app_commands.describe(flag='The flag emoji you wish to set, from the emoji picker. Type "remove" to remove it')
    async def _profile_flag(self, interaction: discord.Interaction, flag: str):
        await interaction.response.defer(ephemeral=True)
        db = mclient.bowser.users
        flag = flag.strip()

        if flag.strip().lower() == 'remove':
            db.update_one({'_id': interaction.user.id}, {'$set': {'regionFlag': None}})
            return await interaction.followup.send(f'{config.greenTick} Your flag has been successfully removed from your profile card! Here\'s how it looks:', file=await self._generate_profile_card_from_member(interaction.user))


        code_points = self.check_flag(flag)
        if code_points is None:
            return await interaction.followup.send(f'{config.redTick} You didn\'t provide a valid supported emoji that represents a flag -- make sure you are providing an emoji, not an abbreviation or text. Please try again; note you can only use emoji like a country\'s flag or extras such as the pirate and gay pride flags')

        # Convert list of ints to lowercase hex code points, seperated by dashes
        pointStr = '-'.join('{:04x}'.format(n) for n in code_points)

        if not Path(f'{self.twemojiPath}{pointStr}.png').is_file():
            return await interaction.followup.send(f'{config.redTick} You didn\'t provide a valid supported emoji that represents a flag -- make sure you are providing an emoji, not an abbreviation or text. Please try again; note you can only use emoji like a country\'s flag or extras such as the pirate and gay pride flags')

        db.update_one({'_id': interaction.user.id}, {'$set': {'regionFlag': pointStr, 'profileSetup': True}})
        await interaction.followup.send(f'{config.greenTick} Your flag has been successfully updated on your profile card! Here\'s how it looks:', file=await self._generate_profile_card_from_member(interaction.user))

    async def _profile_timezone_autocomplete(self, interaction: discord.Interaction, current: str):
        removal = app_commands.Choice(name='Remove your timezone (This will prevent you from using LFG)', value='remove')
        if current:
            extraction = process.extract(current.lower(), pytz.all_timezones, limit=9)
            return [removal] + [
                app_commands.Choice(name=e[0], value=e[0]) for e in extraction
                ]

        else:
            return [removal] + [
                app_commands.Choice(name=tz, value=tz) for tz in self.commonTimezones[0:9]
                ]

    @social_group.command(name='timezone', description='Pick your timezone to show on your profile and for when others are looking for group')
    @app_commands.describe(timezone='This is based on your region. I.e. "America/New_York. Type "remove" to remove it')
    @app_commands.autocomplete(timezone=_profile_timezone_autocomplete)
    async def _profile_timezone(self, interaction: discord.Interaction, timezone: str):
        await interaction.response.defer(ephemeral=True)
    
        db = mclient.bowser.users
    
        if timezone.strip().lower() == 'remove':
            db.update_one({'_id': interaction.user.id}, {'$set': {'timezone': None}})
            return await interaction.followup.send(f'{config.greenTick} Your timezone has been successfully removed from your profile card! Here\'s how it looks:', file=await self._generate_profile_card_from_member(interaction.user))

        for tz in pytz.all_timezones:
            if timezone.lower() == tz.lower():
                db.update_one({'_id': interaction.user.id}, {'$set': {'timezone': tz, 'profileSetup': True}})
                return await interaction.followup.send(f'{config.greenTick} Your timezone has been successfully updated on your profile card! Here\'s how it looks:', file=await self._generate_profile_card_from_member(interaction.user))

        await interaction.followup.send(f'{config.redTick} The timezone you provided is invalid. It should be in the format similar to `America/New_York`. If you aren\'t sure how to find it or what yours is, you can visit [this helpful website](https://www.timezoneconverter.com/cgi-bin/findzone.tzc')

    async def _profile_games_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self.Games._games_search_autocomplete(interaction, current)

    @social_group.command(name='games', description='Pick up-to 3 of your fav Nintendo Switch games to show them off')
    @app_commands.describe(
        game1='You need to pick at least one game. Search by name and use autocomplete to help!',
        game2='Optionally pick a 2nd game to show on your profile as well. Search by name and use autocomplete to help!',
        game3='Optionally pick a 3rd game to show on your profile as well. Search by name and use autocomplete to help!'
    )
    @app_commands.autocomplete(
        game1=_profile_games_autocomplete,
        game2=_profile_games_autocomplete,
        game3=_profile_games_autocomplete
    )
    async def _profile_games(self, interaction: discord.Interaction, game1: str, game2: typing.Optional[str], game3: typing.Optional[str]):
        await interaction.response.defer(ephemeral=True)

        db = mclient.bowser.games

        # If user selected an auto-complete result, we will be provided the guid automatically which saves effort
        flagConfirmation = False
        gameList = []
        guid1 = db.find_one({'guid': game1})
        guid2 = None if not game2 else db.find_one({'guid': game2})
        guid3 = None if not game3 else db.find_one({'guid': game3})

        def resolve_guid(game_name: str):
            return self.Games.search(game_name)

        async def return_failure(interaction: discord.Interaction, game_name: str):
            return await interaction.followup.send(f'{config.redTick} I was unable to match the game named "{game_name}" with any game released on the Nintendo Switch. Please try again, or contact a moderator if you believe this is in error')

        if not guid1:
            flagConfirmation = True
            guid1 = resolve_guid(game1)
            if not guid1:
                return await return_failure(interaction, game1)

        gameList.append(guid1['guid'])

        if game2 and not guid2:
            flagConfirmation = True
            guid2 = resolve_guid(game2)
            if not guid2:
                return await return_failure(interaction, game2)

        if guid2: gameList.append(guid2['guid'])

        if game3 and not guid3:
            flagConfirmation = True
            guid3 = resolve_guid(game3)
            if not guid3:
                return await return_failure(interaction, game3)

        if guid3: gameList.append(guid3['guid'])

        logging.info(guid1)
        logging.info(guid2)
        logging.info(guid3)

        msg = None
        if flagConfirmation:
            # Double check with the user since we needed to use search confidence to obtain one or more of their games
            embed = discord.Embed(title='Are these games correct?', description='*Use the buttons below to confirm*', color=0xf5ff00)
            embed.add_field(name='Game 1', value=db.find_one({'guid': guid1['guid']})['name'])
            if guid2: embed.add_field(name='Game 2', value=db.find_one({'guid': guid2['guid']})['name'])
            if guid3: embed.add_field(name='Game 3', value=db.find_one({'guid': guid3['guid']})['name'])
            view = tools.NormalConfirmation(timeout=90)

            view.message = await interaction.followup.send(':mag: I needed to do an extra search to find one or more of your games. So that I can make sure I found the correct games for you, please use the **Yes** button if everything looks okay or the **No** button if something doesn\'t look right:', embed=embed, view=view, wait=True)
            msg = view.message
            await view.wait()            

            if view.timedout:
                return await view.message.edit(content=f'{config.redTick} Uh, oh. I didn\'t receive a response back from you in time; your profile\'s favorite games have not been changed. Please rerun the command to try again', embed=None)

            elif not view.value:
                # User selected No
                return await view.message.edit(content=f'{config.redTick} It looks like the games I matched for you were incorrect, sorry about that. Please rerun the command to try again. A tip to a great match is to click on an autocomplete option for each game and to type the title as completely as possible -- this will ensure that the correct game is selected. If you continue to experience difficulty in adding a game, please contact a moderator', embed=None)

        # We are good to commit changes
        userDB = mclient.bowser.users
        logging.info(gameList)
        userDB.update_one({'_id': interaction.user.id}, {'$set': {'favgames': gameList}})
        message_reply = f'{config.greenTick} Your favorite games list has been successfully updated on your profile card! Here\'s how it looks:'

        if msg:
            # Webhooks cannot be edited with a file
            await msg.delete()

        await interaction.followup.send(message_reply, file=await self._generate_profile_card_from_member(interaction.user))


    # @_profile.command(name='edit')
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

            if not self.Games:
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

                result = self.Games.search(response.content.strip())

                if result:
                    if len(userGames) == 0 and dbUser['favgames']:
                        db.update_one({'_id': ctx.author.id}, {'$set': {'favgames': []}})

                    if result['guid'] in userGames:
                        failedFetch = True
                        continue

                    name = self.Games.get_preferred_name(result['guid'])
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
            dbUser_phase5 = db.find_one({'_id': ctx.author.id})

            if 'default' in dbUser_phase5['backgrounds']:
                backgrounds = list(dbUser_phase5['backgrounds'])
                backgrounds.remove('default')
                backgrounds.insert(0, 'default-dark')
                backgrounds.insert(0, 'default-light')

                db.update_one({'_id': ctx.author.id}, {'$set': {'backgrounds': backgrounds}})

                if dbUser_phase5['background'] == 'default':
                    db.update_one({'_id': ctx.author.id}, {'$set': {'background': 'default-light'}})

                dbUser_phase5 = db.find_one({'_id': ctx.author.id})

            loading_message = await message.channel.send('Just a moment...')

            backgrounds = list(dbUser_phase5['backgrounds'])
            preview = self._generate_background_preview(backgrounds)

            await message.channel.send(phase5.format(', '.join(backgrounds)), file=preview)
            await loading_message.delete()

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
        embed.set_author(name=str(ctx.author), icon_url=ctx.author.display_avatar.url)

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

                # Duplicate friend code detection
                friendcode = db.find_one({'_id': ctx.author.id})['friendcode']

                if friendcode:
                    query = db.find({"friendcode": friendcode})

                    if query.count() > 1:
                        hasPuns = False
                        otherUsers = []
                        for user in query:
                            if mclient.bowser.puns.find({'user': user["_id"]}).count() > 0:
                                hasPuns = True

                            if user["_id"] != ctx.author.id:
                                try:
                                    fetchedUser = await self.bot.fetch_user(user["_id"])
                                    otherUsers.append(f'> **{str(fetchedUser)}** ({user["_id"]})')
                                except:
                                    otherUsers.append(f'> {user["_id"]}')

                        if hasPuns:
                            adminChat = self.bot.get_channel(config.adminChannel)
                            others = "\n".join(otherUsers)
                            plural = "that of another user" if (len(otherUsers) == 1) else "those of other users"
                            await adminChat.send(
                                f'🕵️ **{ctx.author}** ({ctx.author.id}) has set a friend code (`{friendcode}`) that matches {plural}: \n{others}'
                            )

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

            loading_message = await mainMsg.channel.send('Just a moment...')
            card = await self._generate_profile_card_from_member(ctx.author)

            await mainMsg.channel.send('You are all set! Your profile has been edited:', file=card)
            await loading_message.delete()
            return

        except asyncio.TimeoutError:
            await mainMsg.delete()
            del self.inprogressEdits[ctx.author.id]
            return await botMsg.edit(
                content=f'{ctx.author.mention} You have taken too long to respond and the edit has been timed out, please run `!profile edit` to start again'
            )

    @_profile.group(name='validate', invoke_without_command=True)
    async def _profile_validate(self, ctx: commands.Context, theme, trophy_bg_opacity):
        if (ctx.guild.get_role(config.moderator) not in ctx.author.roles) and (
            ctx.author.id not in self.validate_allowed_users
        ):
            return await ctx.message.reply(':x: You do not have permission to run this command.', delete_after=15)

        if not ctx.message.attachments:
            return await ctx.message.reply(':x: Missing attachment')

        attach = ctx.message.attachments[0]
        if not attach.content_type == 'image/png' or attach.height != 900 or attach.width != 1600:
            return await ctx.message.reply(':x: Attachment must be a 1600x900 PNG file')

        filename = os.path.splitext(attach.filename)[0]
        safefilename = re.sub(r'[^A-Za-z0-9_-]|^(?=\d)', '_', filename)

        if filename != safefilename:
            return await ctx.message.reply(
                ':x: Filenames cannot start with a number or contain non-alphanumeric characters except for an underscore'
            )

        bg_raw_img = Image.open(io.BytesIO(await attach.read())).convert("RGBA")

        # Check mask
        alpha_test_mask = Image.open("resources/profiles/background-test-mask.png").convert("RGBA")

        mask_data = np.array(alpha_test_mask)
        img_data = np.array(bg_raw_img)

        expected_alpha = mask_data[..., -1] > 127
        really_alpha = img_data[..., -1] < 128

        CORRECT_THRESHOLD = 0.999
        TOTAL_PIXELS = 1600 * 900
        correct_alpha_pixels = np.count_nonzero(expected_alpha == really_alpha)
        percent_correct = correct_alpha_pixels / TOTAL_PIXELS

        if percent_correct < CORRECT_THRESHOLD:
            return await ctx.message.reply(
                ':x: Too many pixels have the incorrect transparency! '
                f'Expected at least {CORRECT_THRESHOLD*100:0.3f}% correct, actually {percent_correct*100:0.3f}%'
            )
        # end check mask

        try:
            bg_rendered = self._render_background_image(bg_raw_img, theme, trophy_bg_opacity)
        except ValueError as e:
            return await ctx.message.reply(f':x: {e}')

        background = {'image': bg_rendered, 'theme': theme}

        profile = {
            'pfp': Image.new('RGB', (250, 250)),
            'display_name': "Lorem Ipsum Dolor Sit Amet, Esq",
            'username': "lorem_ipsum_dolor_sit_amet_esq",
            'regionFlag': "1f3f4-200d-2620-fe0f",  # Pirate flag
            'friendcode': "SW-0000-0000-0000",
            'message_count': "8,675,309",
            'joindate': "Jan. 01, 1970",
            'usertime': "Not specified",
            'trophies': [None] * 15,
            'games': ['3030-88442', '3030-87348', '3030-89546'],  # Games with really long titles
        }

        card = await self._generate_profile_card(profile, background)
        cfgstr = f"```yml\n{safefilename}:\n    theme: {theme}\n    trophy-bg-opacity: {trophy_bg_opacity}```"

        await ctx.message.reply(cfgstr, file=card)

    @_profile_validate.command(name='allow')
    async def _profile_validate_allow(self, ctx, member: tools.ResolveUser):
        self.validate_allowed_users.append(member.id)
        return await ctx.message.reply(f'{config.greenTick} {member} temporarily added to allowlist')

    @commands.has_any_role(config.moderator, config.eh)
    @commands.group(name='trivia')
    async def _trivia(self, ctx):
        return

    @commands.has_any_role(config.moderator, config.eh)
    @_trivia.command(name='award')
    async def _trivia_award(self, ctx, members: commands.Greedy[tools.ResolveUser]):
        '''Increase the trivia award trophy by one tier for one or more users'''
        stats = [0] * len(self.triviaTrophyData)
        failed = []
        msg = await ctx.send(f'{config.loading} Processing awards to {len(members)} member(s)...')
        for m in members:
            try:
                newLevel = await self.modify_trivia_level(m)
                stats[newLevel] += 1

            except IndexError:
                failed.append(f'{m.mention} ({m.id})')

        embed = discord.Embed(title='Command Completion Stats')

        successful = len(members) - len(failed)
        embed.description = f'Trivia awards granted to **{successful}**.{" List of trophies the user(s) now have:" if successful else ""}\n\n'
        for index, count in enumerate(stats):
            if count != 0:
                embed.description += f'{self.triviaTrophyData[index][self.EMOTES]} {self.triviaTrophyData[index][self.INDEX].replace("-", " ").title()}: {count}\n'

        if failed:
            embed.add_field(
                name='Failed to award some trophies',
                value=f'The following users were not updated because they already have the max level trophy:\n\n{", ".join(failed)}',
            )

        await msg.edit(content=f'{config.greenTick} Trivia trophy awards complete.', embed=embed)

    @commands.has_any_role(config.moderator, config.eh)
    @_trivia.command(name='reduce')
    async def _trivia_reduce(self, ctx, members: commands.Greedy[tools.ResolveUser]):
        '''Reduce the trivia award trophy tier by 1 for one or more users. If you are trying to take away the trophy entirely, consider using the "profile revoke" command instead'''
        stats = [0] * len(self.triviaTrophyData)
        failed = []
        msg = await ctx.send(f'{config.loading} Reducing awards from {len(members)} member(s)...')
        for m in members:
            try:
                newLevel = await self.modify_trivia_level(m, regress=True)
                stats[newLevel] += 1

            except IndexError:
                failed.append(f'{m.mention} ({m.id})')

        embed = discord.Embed(title='Command Completion Stats')

        successful = len(members) - len(failed)
        embed.description = f'Trivia awards reduced from **{successful}**.{" List of trophies the user(s) now have:" if successful else ""}\n\n'
        for index, count in enumerate(stats):
            if count != 0:
                embed.description += f'{self.triviaTrophyData[index][self.EMOTES]} {self.triviaTrophyData[index][self.INDEX].replace("-", " ").title()}: {count}\n'

        if failed:
            embed.add_field(
                name='Failed to revoke some trophies',
                value=f'The following users were not updated because they do not have any trivia trophies:\n\n{", ".join(failed)}',
            )

        await msg.edit(content=f'{config.greenTick} Trivia trophy revocation complete.', embed=embed)

    @commands.has_any_role(config.moderator, config.eh)
    @_profile.command(name='grant')
    async def _profile_grant(self, ctx, item: str, members: commands.Greedy[tools.ResolveUser], name: str):
        '''Grants specified item, background or trophy, to a member'''
        item = item.lower()
        name = name.lower()

        if not members:
            return await ctx.send(
                f'{config.redTick} Invalid formatting of members in command: please check your syntax and ensure you are providing at least one valid member then try again. Member(s) should be provided at the end of the command'
            )

        if item not in ['background', 'trophy']:
            return await ctx.send(f'{config.redTick} Invalid item: {item}. Expected either `background` or `trophy`')

        if item == 'background' and name not in self.backgrounds:
            return await ctx.send(f'{config.redTick} Invalid background: {name}')

        if item == 'trophy':
            if not os.path.isfile(f'resources/profiles/trophies/{name}.png'):
                return await ctx.send(f'{config.redTick} Invalid trophy: {name}')

            if name in self.special_trophies:
                return await ctx.send(f'{config.redTick} Trophy cannot be granted via command: {name}')

        msg = await ctx.send(f'{config.loading} Granting {item.title()} `{name}` to {len(members)} member(s)...')
        failCount = 0
        for m in members:
            try:
                await tools.commit_profile_change(self.bot, m, item, name)

            except (ValueError, AttributeError):
                failCount += 1

        if not failCount:
            # 0 Failures
            return await msg.edit(
                content=f'{config.greenTick} {item.title()} `{name}` granted to {len(members)} member(s)'
            )

        elif failCount == len(members):
            return await msg.edit(content=f'{config.redTick} {item.title()} `{name}` granted to 0 members')

        else:
            return await msg.edit(
                content=f'{config.greenTick} {item.title()} `{name}` granted to {len(members) - failCount}/{len(members)} member(s).'
            )

    @commands.has_any_role(config.moderator, config.eh)
    @_profile.command(name='revoke')
    async def _profile_revoke(self, ctx, item: str, members: commands.Greedy[tools.ResolveUser], name: str):
        '''Revokes specified item, background or trophy, from a member'''
        item = item.lower()
        name = name.lower()

        if item not in ['background', 'trophy']:
            return await ctx.send(f'{config.redTick} Invalid item: {item}. Expected either `background` or `trophy`')

        if item == 'trophy' and name in self.special_trophies:
            return await ctx.send(f'{config.redTick} Trophy cannot be revoked via command: {name}')

        msg = await ctx.send(f'{config.loading} Revoking {item.title()} `{name}` from {len(members)} member(s)...')
        failCount = 0
        for m in members:
            try:
                await tools.commit_profile_change(self.bot, m, item, name, revoke=True)

            except (ValueError, AttributeError):
                failCount += 1

        if not failCount:
            # 0 Failures
            return await msg.edit(
                content=f'{config.greenTick} {item.title()} `{name}` revoked from {len(members)} member(s)'
            )

        elif failCount == len(members):
            return await msg.edit(content=f'{config.redTick} {item.title()} `{name}` revoked from 0 members')

        else:
            return await msg.edit(
                content=f'{config.greenTick} {item.title()} `{name}` revoked from {len(members) - failCount}/{len(members)} member(s).'
            )

    @commands.Cog.listener()
    async def on_message(self, message):
        if (not message.guild) or message.author.bot:
            return

        content = re.sub(r'(<@!?\d+>)', '', message.content)
        contains_code = tools.re_match_nonlink(self.friendCodeRegex['chatFilter'], content)

        if not contains_code:
            return
        if message.channel.id not in [config.commandsChannel]:
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


async def setup(bot):
    await bot.add_cog(SocialFeatures(bot))
    logging.info('[Extension] Social module loaded')


async def teardown(bot):
    await bot.remove_cog('SocialFeatures')
    logging.info('[Extension] Social module unloaded')
