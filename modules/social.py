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

        self.Games = self.bot.get_cog('Games')

        # !profile ratelimits
        self.bucket_storage = token_bucket.MemoryStorage()
        self.profile_bucket = token_bucket.Limiter(1 / 30, 2, self.bucket_storage)  # burst limit 2, renews at 1 / 30 s

        # Add context menus to command tree
        self.profileContextMenu = app_commands.ContextMenu(
            name='View Profile Card', callback=self._profile_view, type=discord.AppCommandType.user
        )
        self.bot.tree.add_command(self.profileContextMenu, guild=discord.Object(id=config.nintendoswitch))

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
                Image.open(f'resources/profiles/layout/{theme}/missing-game.png').convert("RGBA").resize((120, 120))
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
            "autocomplete": re.compile(
                r'(?:sw)?[ \-\u2014_]?(\d{1,4})[ \-\u2014_]?(\d{0,4})[ \-\u2014_]?(\d{0,4})', re.I
            ),
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

        await self._profile_view(interaction, member)

    async def _profile_view(self, interaction: discord.Interaction, member: discord.Member):
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
        gameUnderline = Image.open(f'resources/profiles/layout/{theme_name}/trophy-case-underline.png').convert("RGBA")
        trophyUnderline = Image.open(f'resources/profiles/layout/{theme_name}/favorite-games-underline.png').convert(
            "RGBA"
        )

        img.paste(snoo, (50, 50), snoo)
        img.paste(trophyUnderline, (60, 610), trophyUnderline)
        img.paste(gameUnderline, (1150, 95), gameUnderline)

        draw = ImageDraw.Draw(img)
        self._draw_text(draw, (150, 51), '/r/NintendoSwitch Discord', theme['branding'], fonts['meta'])
        self._draw_text(draw, (150, 91), 'User Profile', theme['secondary_heading'], fonts['meta'])
        self._draw_text(draw, (60, 460), 'Member since', theme['secondary_heading'], fonts['small'])
        self._draw_text(draw, (435, 460), 'Messages sent', theme['secondary_heading'], fonts['small'])
        self._draw_text(draw, (790, 460), 'Local time', theme['secondary_heading'], fonts['small'])
        self._draw_text(draw, (1150, 42), 'Favorite games', theme['primary_heading'], fonts['medium'])
        self._draw_text(draw, (60, 557), 'Trophy case', theme['primary_heading'], fonts['medium'])

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

    async def _cache_game_img(self, guid: str, theme) -> Image:
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
                    gameIcon = Image.open(gameImg).convert('RGBA').resize((120, 120))
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
            setGames = setGames[0:5]  # Limit to 5 results, just in case

            message_count = f'{mclient.bowser.messages.count_documents({"author": member.id}):,}'

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

        if len(trophies) < 18:  # Check for additional non-prefered trophies
            for x in dbUser['trophies']:
                if x not in trophies:
                    trophies.append(x)

        while len(trophies) < 18:
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

        self._draw_text(draw, (435, 490), profile['message_count'], theme["primary"], fonts['medium'])
        self._draw_text(draw, (60, 490), profile['joindate'], theme["primary"], fonts['medium'])
        self._draw_text(draw, (790, 490), profile['usertime'], theme["primary"], fonts['medium'])

        # Start trophies
        trophyLocations = {
            0: (60, 630),
            1: (171, 630),
            2: (283, 630),
            3: (394, 630),
            4: (505, 630),
            5: (616, 630),
            6: (728, 630),
            7: (839, 630),
            8: (950, 630),
            9: (60, 745),
            10: (171, 745),
            11: (283, 745),
            12: (394, 745),
            13: (505, 745),
            14: (616, 745),
            15: (728, 745),
            16: (839, 745),
            17: (950, 745),
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
        gameIconLocations = {0: (1150, 130), 1: (1150, 280), 2: (1150, 430), 3: (1150, 580), 4: (1150, 730)}
        gameTextLocations = {0: 130, 1: 280, 2: 430, 3: 580, 4: 730}

        setGames = profile['games']
        gameCount = 0
        if setGames:
            setGames = list(dict.fromkeys(setGames))  # Remove duplicates from list, just in case
            setGames = setGames[:5]  # Limit to 5 results, just in case

            for game_guid in setGames:
                if not self.Games:
                    continue

                gameName = self.Games.get_preferred_name(game_guid)

                if not gameName:
                    continue

                gameIcon = await self._cache_game_img(game_guid, theme)
                card.paste(gameIcon, gameIconLocations[gameCount], gameIcon)

                nameW = 1285
                nameWMax = 1525

                game_name_font = fonts['medium'][self._determine_cjk_font(gameName)]

                # Word wrap logic with overflow protection
                words = gameName.split()
                lines = []
                current_line = []
                current_w = 0

                # Use nameW as the starting X coordinate for all lines
                start_x = nameW
                max_w = nameWMax - start_x
                space_w = game_name_font.getsize(' ')[0]

                for word in words:
                    word_w = game_name_font.getsize(word)[0]

                    # Handle massive words that don't fit on a single line
                    if word_w > max_w:
                        if current_line:
                            lines.append(' '.join(current_line))
                            current_line = []
                            current_w = 0

                        # Split the long word by character
                        partial_word = ""
                        partial_w = 0
                        for char in word:
                            char_w = game_name_font.getsize(char)[0]
                            if partial_w + char_w > max_w:
                                lines.append(partial_word)
                                partial_word = char
                                partial_w = char_w
                            else:
                                partial_word += char
                                partial_w += char_w

                        if partial_word:
                            current_line = [partial_word]
                            current_w = partial_w + space_w

                    # Handle normal words
                    elif current_w + word_w <= max_w:
                        current_line.append(word)
                        current_w += word_w + space_w
                    else:
                        if current_line:
                            lines.append(' '.join(current_line))
                        current_line = [word]
                        current_w = word_w + space_w

                if current_line:
                    lines.append(' '.join(current_line))

                # Safety: Limit to 3 lines and trim for ellipsis
                if len(lines) > 3:
                    lines = lines[:3]
                    ellipsis = "..."
                    ellipsis_w = game_name_font.getsize(ellipsis)[0]

                    # Shrink the last line until "..." fits
                    while lines[-1]:
                        current_line_w = game_name_font.getsize(lines[-1])[0]
                        if current_line_w + ellipsis_w <= max_w:
                            break
                        lines[-1] = lines[-1][:-1]  # Remove last char

                    lines[-1] += ellipsis

                # Safety: Limit to 3 lines
                if len(lines) > 3:
                    lines = lines[:3]
                    lines[-1] += "..."

                # Draw the wrapped lines
                # Use gameTextLocations[gameCount] as the starting Y
                y_pos = gameTextLocations[gameCount]

                for line in lines:
                    # Draw text at the stored start_x
                    draw.text((start_x, y_pos), line, tuple(theme["primary"]), font=game_name_font)
                    y_pos += 40  # Increase height by 40px for next line

                gameCount += 1
        if gameCount == 0:  # No games rendered
            self._draw_text(draw, (1150, 130), 'Not specified', theme["secondary_heading"], fonts['medium'])

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
            BLACK_FLAG_EMOJI = u'\U0001f3f4'
            TAG_CHARACTERS = [chr(c) for c in range(ord('\U000e0020'), ord('\U000e007e') + 1)]
            TAG_TERMINATOR = u'\U000e007f'

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
        """
        Generates a discord.Embed with information profile card editing flow and all subcommands.

        returns discord.Embed, discord.File
        """

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
            f'\n- **Pick a Timezone**: </profile timezone:{commandID}> Let others know what time it is for you and your timezone. You can find yours by clicking [here](https://www.timezoneconverter.com/cgi-bin/findzone.tzc).'
            f'\n- **Rep a Flag**: </profile flag:{commandID}> Show your country 🇺🇳, be a pirate 🏴‍☠️, or rep pride 🏳️‍🌈 with flag emoji on your card!'
            f'\n- **Show Off Your Fav Games**: </profile games:{commandID}> Show off up-to 5 of your Switch game faves.'
            f'\n- **Choose a Different Background**: </profile background:{commandID}> Start with a light or dark theme. '
            'Earn more in events (like Trivia) to make your card pop!'
            '\n**Get Some Trophies**\nEarn a trophy when you participate in server events and Trivia!\n'
            'They\'ll show up automatically on your card when assigned by a moderator\n\n'
            'Default profiles are boring! Spruce it up!\n__Here\'s how your card currently looks:__'
        )
        embed.description = embed_description

        return embed, main_img  # Both need to be passed into a message for image embedding to function

    @commands.group(name='profile', invoke_without_command=True)
    async def _old_profile_redirect(self, ctx):
        for command in self.bot.tree.get_commands(guild=discord.Object(id=config.nintendoswitch)):
            # Iterate over commands in the tree so we can get the profile command ID
            if command.name == 'profile':
                break

        commandStr = f'</profile view:{command.extras["id"]}>'
        await ctx.message.reply(
            f':repeat: Hi there! I no longer use text commands. Instead, please repeat your command using {commandStr} as a slash command instead',
            delete_after=10,
        )
        await ctx.message.delete()

    @_old_profile_redirect.command(name='edit')
    async def _old_profile_redirect_edit(self, ctx):
        for command in self.bot.tree.get_commands(guild=discord.Object(id=config.nintendoswitch)):
            # Iterate over commands in the tree so we can get the profile command ID
            if command.name == 'profile':
                break

        commandStr = f'</profile edit:{command.extras["id"]}>'
        await ctx.message.reply(
            f':repeat: Hi there! I no longer use text commands. Instead, please repeat your command using {commandStr} as a slash command instead',
            delete_after=10,
        )
        await ctx.message.delete()

    async def _profile_friendcode_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> typing.List[app_commands.Choice[str]]:
        partialCode = re.search(self.friendCodeRegex['autocomplete'], current)

        def pad_extra_chars(partial_code: str):
            length = len(partial_code)
            if length < 4:
                partial_code += '#' * (4 - length)

            return partial_code

        # Build a result
        friendcode = 'SW-'
        if not partialCode:
            # No match at all, return a default value
            return [app_commands.Choice(name='SW-####-####-####', value='SW-####-####-####')]

        friendcode += pad_extra_chars(partialCode.group(1))

        if partialCode.group(2):
            friendcode += '-' + pad_extra_chars(partialCode.group(2))
            if partialCode.group(3):
                friendcode += '-' + pad_extra_chars(partialCode.group(3))

            else:
                friendcode += '-####'

        else:
            friendcode += '-####-####'

        return [app_commands.Choice(name=friendcode, value=friendcode)]

    @social_group.command(
        name='friendcode', description='Use this command to edit the displayed friend code on your profile'
    )
    @app_commands.describe(code='Update your Switch Friend code, formatted as SW-0000-0000-0000')
    @app_commands.autocomplete(code=_profile_friendcode_autocomplete)
    async def _profile_friendcode(self, interaction: discord.Interaction, code: str):
        await interaction.response.defer(ephemeral=True)
        db = mclient.bowser.users

        friendcode = re.search(self.friendCodeRegex['profile'], code)
        if friendcode:  # re match
            friendcode = f'SW-{friendcode.group(1)}-{friendcode.group(2)}-{friendcode.group(3)}'
            if friendcode == 'SW-0000-0000-0000':
                return await interaction.followup.send(
                    f'{config.redTick} The Nintendo Switch friend code you provided is invalid, please try again. The format of a friend code is `SW-0000-0000-0000`, with the zeros replaced with the numbers from your unique code'
                )

            db.update_one({'_id': interaction.user.id}, {'$set': {'friendcode': friendcode, 'profileSetup': True}})

            msg = f'{config.greenTick} Your friend code has been successfully updated on your profile card! Here\'s how it looks:'

            # Duplicate friend code detection
            duplicates = db.find({'_id': {'$ne': interaction.user.id}, 'friendcode': friendcode})

            if duplicates:
                # Check if accounts with matching friend codes have infractions on file
                punsDB = mclient.bowser.puns
                hasPuns = False
                otherUsers = []
                for u in duplicates:
                    if punsDB.count_documents({'user': u['_id']}):
                        hasPuns = True

                    if interaction.user.id != u['_id']:
                        user = interaction.guild.get_member(u['_id'])
                        if not user:
                            user = await self.bot.fetch_user(u['_id'])

                        otherUsers.append(f'> **{user}** ({u["_id"]})')

                if hasPuns:
                    admin_channel = self.bot.get_channel(config.adminChannel)
                    others = '\n'.join(otherUsers)
                    plural = "that of another user" if (len(otherUsers) == 1) else "those of other users"
                    await admin_channel.send(
                        f'🕵️ **{interaction.user}** ({interaction.user.id}) has set a friend code (`{friendcode}`) that matches {plural}: \n{others}'
                    )

        else:
            return await interaction.followup.send(
                f'{config.redTick} The Nintendo Switch friend code you provided is invalid, please try again. The format of a friend code is `SW-0000-0000-0000`, with the zeros replaced with the numbers from your unique code'
            )

        await interaction.followup.send(msg, file=await self._generate_profile_card_from_member(interaction.user))

    @social_group.command(name='flag', description='Choose an emoji flag to display on your profile')
    @app_commands.describe(flag='The flag emoji you wish to set, from the emoji picker')
    async def _profile_flag(self, interaction: discord.Interaction, flag: str):
        await interaction.response.defer(ephemeral=True)
        db = mclient.bowser.users
        flag = flag.strip()

        code_points = self.check_flag(flag)
        if code_points is None:
            return await interaction.followup.send(
                f'{config.redTick} You didn\'t provide a valid supported emoji that represents a flag -- make sure you are providing an emoji, not an abbreviation or text. Please try again; note you can only use emoji like a country\'s flag or extras such as the pirate and gay pride flags'
            )

        # Convert list of ints to lowercase hex code points, seperated by dashes
        pointStr = '-'.join('{:04x}'.format(n) for n in code_points)

        if not Path(f'{self.twemojiPath}{pointStr}.png').is_file():
            return await interaction.followup.send(
                f'{config.redTick} You didn\'t provide a valid supported emoji that represents a flag -- make sure you are providing an emoji, not an abbreviation or text. Please try again; note you can only use emoji like a country\'s flag or extras such as the pirate and gay pride flags'
            )

        db.update_one({'_id': interaction.user.id}, {'$set': {'regionFlag': pointStr, 'profileSetup': True}})
        await interaction.followup.send(
            f'{config.greenTick} Your flag has been successfully updated on your profile card! Here\'s how it looks:',
            file=await self._generate_profile_card_from_member(interaction.user),
        )

    async def _profile_timezone_autocomplete(self, interaction: discord.Interaction, current: str):
        if current:
            extraction = process.extract(current.lower(), pytz.all_timezones, limit=9)
            return [app_commands.Choice(name=e[0], value=e[0]) for e in extraction]

        else:
            return [app_commands.Choice(name=tz, value=tz) for tz in self.commonTimezones[0:9]]

    @social_group.command(
        name='timezone',
        description='Pick your timezone to show on your profile and for when others are looking for group',
    )
    @app_commands.describe(timezone='This is based on your region. I.e. "America/New_York')
    @app_commands.autocomplete(timezone=_profile_timezone_autocomplete)
    async def _profile_timezone(self, interaction: discord.Interaction, timezone: str):
        await interaction.response.defer(ephemeral=True)

        db = mclient.bowser.users
        for tz in pytz.all_timezones:
            if timezone.lower() == tz.lower():
                db.update_one({'_id': interaction.user.id}, {'$set': {'timezone': tz, 'profileSetup': True}})
                return await interaction.followup.send(
                    f'{config.greenTick} Your timezone has been successfully updated on your profile card! Here\'s how it looks:',
                    file=await self._generate_profile_card_from_member(interaction.user),
                )

        await interaction.followup.send(
            f'{config.redTick} The timezone you provided is invalid. It should be in the format similar to `America/New_York`. If you aren\'t sure how to find it or what yours is, you can visit [this helpful website](https://www.timezoneconverter.com/cgi-bin/findzone.tzc)'
        )

    @social_group.command(name='games', description='Pick up-to 5 of your fav Nintendo Switch games to show them off')
    @app_commands.describe(
        game1='You need to pick at least one game. Search by name',
        game2='Optionally pick a 2nd game to show on your profile as well. Search by name',
        game3='Optionally pick a 3rd game to show on your profile as well. Search by name',
        game4='Optionally pick a 4th game to show on your profile as well. Search by name',
        game5='Optionally pick a 5th game to show on your profile as well. Search by name',
    )
    async def _profile_games(
        self,
        interaction: discord.Interaction,
        game1: str,
        game2: typing.Optional[str],
        game3: typing.Optional[str],
        game4: typing.Optional[str],
        game5: typing.Optional[str],
    ):
        await interaction.response.defer(ephemeral=True)

        db = mclient.bowser.games

        # If user selected an auto-complete result, we will be provided the guid automatically which saves effort
        flagConfirmation = False
        gameList = []
        guid1 = db.find_one({'guid': game1})
        guid2 = None if not game2 else db.find_one({'guid': game2})
        guid3 = None if not game3 else db.find_one({'guid': game3})
        guid4 = None if not game4 else db.find_one({'guid': game4})
        guid5 = None if not game5 else db.find_one({'guid': game5})

        games = [game1, game2, game3, game4, game5]
        guids = [guid1, guid2, guid3, guid4, guid5]

        def resolve_guid(game_name: str):
            return self.Games.search(game_name)

        async def return_failure(interaction: discord.Interaction, game_name: str):
            return await interaction.followup.send(
                f'{config.redTick} I was unable to match the game named "{game_name}" with any game released on the Nintendo Switch. Please try again, or contact a moderator if you believe this is in error'
            )

        flagConfirmation = False
        for idx, guid in enumerate(guids):
            if not games[idx]:
                continue

            if not guid:
                flagConfirmation = True
                guid = resolve_guid(games[idx])

            if not guid:
                return await return_failure(interaction, games[idx])

            gameList.append(guid['guid'])

        msg = None
        if flagConfirmation:
            # Double check with the user since we needed to use search confidence to obtain one or more of their games
            embed = discord.Embed(
                title='Are these games correct?', description='*Use the buttons below to confirm*', color=0xF5FF00
            )
            for idx, game in enumerate(gameList):
                embed.add_field(name=f'Game {idx + 1}', value=db.find_one({'guid': game})['name'])

            view = tools.NormalConfirmation(timeout=90.0)
            view.message = await interaction.followup.send(
                ':mag: I needed to do an extra search to find one or more of your games. So that I can make sure I found the correct games for you, please use the **Yes** button if everything looks okay or the **No** button if something doesn\'t look right:',
                embed=embed,
                view=view,
                wait=True,
            )
            msg = view.message
            timedOut = await view.wait()

            if timedOut:
                return await view.message.edit(
                    content=f'{config.redTick} Uh, oh. I didn\'t receive a response back from you in time; your profile\'s favorite games have not been changed. Please rerun the command to try again',
                    embed=None,
                )

            elif not view.value:
                # User selected No
                return await view.message.edit(
                    content=f'{config.redTick} It looks like the games I matched for you were incorrect, sorry about that. Please rerun the command to try again. A tip to a great match is to click on an autocomplete option for each game and to type the title as completely as possible -- this will ensure that the correct game is selected. If you continue to experience difficulty in adding a game, please contact a moderator',
                    embed=None,
                )

        # We are good to commit changes
        userDB = mclient.bowser.users
        userDB.update_one({'_id': interaction.user.id}, {'$set': {'favgames': gameList}})
        message_reply = f'{config.greenTick} Your favorite games list has been successfully updated on your profile card! Here\'s how it looks:'

        if msg:
            # Webhooks cannot be edited with a file
            await msg.delete()

        await interaction.followup.send(
            message_reply, file=await self._generate_profile_card_from_member(interaction.user), ephemeral=True
        )

    class BackgroundSelectMenu(discord.ui.View):
        message: discord.Message | None = None

        def __init__(self, Parent, options: list[discord.SelectOption], initial_interaction: discord.Interaction):
            super().__init__(timeout=180.0)
            self.Parent = Parent

            self.menus = []
            amt = len(options)
            amt_req = math.ceil(amt / 25)  # Choice elements have a maximum of 25 items
            if amt_req > 125:
                # Don't want to think about what to do if this happens.
                # We'd have the max menus on this message already
                logging.error(
                    f'[Social] BackgroundSelectMenu received a request for > 125 backgrounds, out of range. {amt} | id {initial_interaction.user.id}'
                )
                raise IndexError(
                    f'BackgroundSelectMenu received a request for > 125 backgrounds, out of range. {amt} | id {initial_interaction.user.id}'
                )

            for x in range(amt_req):
                x += 1
                choices = options[(x - 1) * 25 : x * 25]  # Make sure we need 25 long indexes i.e. [0:25], [25:50]
                menu = discord.ui.Select(placeholder='Choose a background', options=choices, min_values=0, max_values=1)
                menu.callback = self.select_option
                self.add_item(menu)
                self.menus.append(menu)

            button = discord.ui.Button(label='Cancel', style=discord.ButtonStyle.secondary)
            button.callback = self.cancel_button
            self.add_item(button)
            initial_interaction.client.loop.call_soon(
                initial_interaction.client.loop.create_task, initial_interaction.edit_original_response(view=self)
            )

        async def select_option(self, interaction: discord.Interaction):
            for s in self.menus:
                if s.values:
                    value = s.values[0]
                    db = mclient.bowser.users
                    db.update_one({'_id': interaction.user.id}, {'$set': {'background': value}})

                    await self.message.delete()
                    await interaction.response.send_message(
                        f'{config.greenTick} Your background has been successfully updated on your profile card! Here\'s how it looks:',
                        file=await self.Parent._generate_profile_card_from_member(interaction.user),
                        embed=None,
                        ephemeral=True,
                    )
                    self.stop()
                    break

            if not s.values:
                await interaction.response.edit_message(view=self)

        async def cancel_button(self, interaction: discord.Interaction):
            await interaction.response.edit_message(
                content='Background editing canceled. To begin again, rerun the command',
                attachments=[],
                embed=None,
                view=None,
            )
            self.stop()

        async def on_timeout(self):
            if self.message and not self.is_finished():
                await self.message.edit(
                    content='Background editing timed out. To begin again, rerun the command',
                    attachments=[],
                    embed=None,
                    view=None,
                )

    @social_group.command(name='background', description='Update the background you use on your profile card')
    async def _profile_background(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        db = mclient.bowser.users
        user = db.find_one({'_id': interaction.user.id})
        bg = user['background']

        choices = []
        formattedBgs = []
        backgrounds = list(reversed(user['backgrounds']))

        for background in backgrounds:
            name = background.replace('-', ' ').title()
            choices.append(discord.SelectOption(label=name, value=background, default=bg == background))
            formattedBgs.append(name)

        human_backgrounds = ', '.join(formattedBgs)
        view = self.BackgroundSelectMenu(
            self,
            choices,
            interaction,
        )
        embed = discord.Embed(url='http://rnintendoswitch.com', color=0x8BC062)
        embed.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)
        embed.description = (
            '**Let\'s Choose a New Profile Background**\nUsing the select menus below, you can choose a new profile background!'
            f' You currently you access to:\n\n> {human_backgrounds}\nExamples of all these backgrounds are:'
        )
        embed.set_image(url='attachment://preview.png')

        msg = await interaction.followup.send(
            embeds=[embed], file=self._generate_background_preview(backgrounds), view=view, wait=True
        )
        view.message = msg
        # await view.wait()

    @social_group.command(
        name='remove', description='Remove or reset an element on your profile card, i.e. your friend code or fav games'
    )
    @app_commands.describe(element='The part of your profile card you which to remove or reset')
    async def _profile_remove(
        self,
        interaction: discord.Interaction,
        element: typing.Literal['Friend Code', 'Flag', 'Timezone', 'Favorite Games', 'Background'],
    ):
        await interaction.response.defer(ephemeral=True)
        elementKeyPairs = {
            'Friend Code': ('friendcode', 'has'),
            'Flag': ('regionFlag', 'has'),
            'Timezone': ('timezone', 'has'),
            'Favorite Games': ('favgames', 'have'),
            'Background': ('background', 'has'),
        }

        db = mclient.bowser.users
        msg = f'Your {element.lower()} {elementKeyPairs[element][1]} been removed from your profile successfully'
        if element == 'Favorite Games':
            db.update_one({'_id': interaction.user.id}, {'$set': {'favgames': []}})

        elif element == 'Background':
            db.update_one({'_id': interaction.user.id}, {'$set': {'background': 'default-light'}})
            msg += ', and has been set to `Default Light` theme. '

        else:
            db.update_one({'_id': interaction.user.id}, {'$set': {elementKeyPairs[element][0]: None}})
            msg += '. '

        msg += 'Here\'s how it looks:'
        await interaction.followup.send(msg, file=await self._generate_profile_card_from_member(interaction.user))

    @social_group.command(
        name='edit', description='Run this command for help with editing your profile and what the other commands do'
    )
    async def _profile_edit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        db = mclient.bowser.users
        u = db.find_one({'_id': interaction.user.id})
        embed, card = await self.generate_user_flow_embed(interaction.user, new_user=not u['profileSetup'])
        await interaction.followup.send(embed=embed, file=card)

    @app_commands.guilds(discord.Object(id=config.nintendoswitch))
    @app_commands.default_permissions(view_audit_log=True)
    @app_commands.checks.has_any_role(config.moderator, config.eh)
    class ProfileManageCommand(app_commands.Group):
        pass

    profile_manage_group = ProfileManageCommand(
        name='manage-profile', description='Higher level commands to manage the profile system'
    )

    @profile_manage_group.command(name='validate', description='Validate a new background with selected opacity')
    async def _profile_validate(
        self,
        interaction: discord.Interaction,
        attach: discord.Attachment,
        theme: typing.Literal['light', 'dark'],
        bg_opacity: int,
    ):
        await interaction.response.defer()
        if not attach.content_type == 'image/png' or attach.height != 900 or attach.width != 1600:
            return await interaction.followup.send(':x: Attachment must be a 1600x900 PNG file', ephemeral=True)

        filename = os.path.splitext(attach.filename)[0]
        safefilename = re.sub(r'[^A-Za-z0-9_-]|^(?=\d)', '_', filename)

        if filename != safefilename:
            return await interaction.followup.send(
                ':x: Filenames cannot start with a number or contain non-alphanumeric characters except for an underscore',
                ephemeral=True,
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
            return await interaction.followup.send(
                ':x: Too many pixels have the incorrect transparency! '
                f'Expected at least {CORRECT_THRESHOLD*100:0.3f}% correct, actually {percent_correct*100:0.3f}%',
                ephemeral=True,
            )
        # end check mask

        try:
            bg_rendered = self._render_background_image(bg_raw_img, theme, bg_opacity)
        except ValueError as e:
            return await interaction.followup.send(f'{config.redTick} {e}', ephemeral=True)

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
            'trophies': [None] * 18,
            'games': [
                '3030-88442',
                '3030-87348',
                '3030-89546',
                '3030-84825',
                '3030-89623',
            ],  # Games with really long titles
        }

        card = await self._generate_profile_card(profile, background)
        cfgstr = f"```yml\n{safefilename}:\n    theme: {theme}\n    trophy-bg-opacity: {bg_opacity}```"

        await interaction.followup.send(cfgstr, file=card)

    @app_commands.guilds(discord.Object(id=config.nintendoswitch))
    @app_commands.default_permissions(view_audit_log=True)
    @app_commands.checks.has_any_role(config.moderator, config.eh)
    class TriviaCommand(app_commands.Group):
        pass

    trivia_group = TriviaCommand(name='trivia', description='Manage trivia awards for members')

    @trivia_group.command(
        name='award', description='Increase the trivia award trophy by one tier for one or more users'
    )
    @app_commands.describe(members='The user or users you wish to award. Must be user ids separated by a space')
    async def _trivia_award(self, interaction, members: str):
        '''Increase the trivia award trophy by one tier for one or more users'''
        stats = [0] * len(self.triviaTrophyData)
        failed = []
        members = members.split()
        await interaction.response.send_message(f'{config.loading} Processing awards to {len(members)} member(s)...')
        for m in members:
            try:
                user = int(m)
                m = interaction.guild.get_member(user)
                if not m:
                    try:
                        m = self.bot.fetch_user(user)

                    except:
                        failed.append(f'{user}')
                        continue

                newLevel = await self.modify_trivia_level(m)
                stats[newLevel] += 1

            except IndexError:
                failed.append(f'{m.mention} ({m.id})')

            except ValueError:
                failed.append(str(user))

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

        await interaction.edit_original_response(
            content=f'{config.greenTick} Trivia trophy awards complete.', embed=embed
        )

    @trivia_group.command(
        name='reduce',
        description='Reduce the trivia award of a user by 1 tier. Consider profile revoke to fully remove',
    )
    @app_commands.describe(members='The user or users you wish to reduce. Must be user ids separated by a space')
    async def _trivia_reduce(self, interaction: discord.Interaction, members: str):
        '''Reduce the trivia award trophy tier by 1 for one or more users. If you are trying to take away the trophy entirely, consider using the "profile revoke" command instead'''
        stats = [0] * len(self.triviaTrophyData)
        failed = []
        members = members.split()
        await interaction.response.send_message(f'{config.loading} Reducing awards from {len(members)} member(s)...')
        for m in members:
            try:
                user = int(m)
                m = interaction.guild.get_member(user)
                if not m:
                    try:
                        m = self.bot.fetch_user(user)

                    except:
                        failed.append(f'{user}')
                        continue

                newLevel = await self.modify_trivia_level(m, regress=True)
                stats[newLevel] += 1

            except IndexError:
                failed.append(f'{m.mention} ({m.id})')

            except ValueError:
                failed.append(str(user))

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

        await interaction.edit_original_response(
            content=f'{config.greenTick} Trivia trophy revocation complete.', embed=embed
        )

    @profile_manage_group.command(
        name='grant', description='Grants a specified item, background, or trophy to a member'
    )
    @app_commands.describe(
        members='The user or users you wish to grant items. Must be user ids separated by a space',
        item='Which profile element you wish to modify',
        name='Name of the element to modify',
    )
    async def _profile_grant(
        self, interaction: discord.Interaction, members: str, item: typing.Literal['background', 'trophy'], name: str
    ):
        '''Grants specified item, background or trophy, to a member'''
        await interaction.response.defer()
        item = item.lower()
        name = name.lower()

        members = members.split()
        users = []
        for m in members:
            try:
                member = interaction.guild.get_member(int(m))
                if not member:
                    member = await self.bot.fetch_user(int(m))

                users.append(member)

            except ValueError:
                return await interaction.followup.send(f'{config.redTick} Provided user {m} is invalid', ephemeral=True)

        if item == 'background' and name not in self.backgrounds:
            return await interaction.followup.send(f'{config.redTick} Invalid background: {name}', ephemeral=True)

        if item == 'trophy':
            if not os.path.isfile(f'resources/profiles/trophies/{name}.png'):
                return await interaction.followup.send(f'{config.redTick} Invalid trophy: {name}', ephemeral=True)

            if name in self.special_trophies:
                return await interaction.followup.send(
                    f'{config.redTick} Trophy cannot be granted via command: {name}', ephemeral=True
                )

        msg = await interaction.followup.send(
            f'{config.loading} Granting {item.title()} `{name}` to {len(users)} member(s)...', wait=True
        )
        failCount = 0
        for m in users:
            try:
                await tools.commit_profile_change(self.bot, m, item, name)

            except (ValueError, AttributeError):
                failCount += 1

        if not failCount:
            # 0 Failures
            return await msg.edit(
                content=f'{config.greenTick} {item.title()} `{name}` granted to {len(users)} member(s)'
            )

        elif failCount == len(users):
            return await msg.edit(content=f'{config.redTick} {item.title()} `{name}` granted to 0 members')

        else:
            return await msg.edit(
                content=f'{config.greenTick} {item.title()} `{name}` granted to {len(users) - failCount}/{len(users)} member(s).'
            )

    @profile_manage_group.command(
        name='revoke', description='Revokes a specified item, background, or trophy from a member'
    )
    @app_commands.describe(
        members='The user or users you wish to revoke items. Must be user ids separated by a space',
        item='Which profile element you wish to modify',
        name='Name of the element to modify',
    )
    async def _profile_revoke(
        self, interaction: discord.Interaction, members: str, item: typing.Literal['background', 'trophy'], name: str
    ):
        '''Revokes specified item, background or trophy, from a member'''
        await interaction.response.defer()
        item = item.lower()
        name = name.lower()
        members = members.split()
        users = []
        for m in members:
            try:
                member = interaction.guild.get_member(int(m))
                if not member:
                    member = await self.bot.fetch_user(int(m))

                users.append(member)

            except ValueError:
                return await interaction.followup.send(f'{config.redTick} Provided user {m} is invalid', ephemeral=True)

        if item == 'trophy' and name in self.special_trophies:
            return await interaction.followup.send(
                f'{config.redTick} Trophy cannot be revoked via command: {name}', ephemeral=True
            )

        msg = await interaction.followup.send(
            f'{config.loading} Revoking {item.title()} `{name}` from {len(members)} member(s)...', wait=True
        )
        failCount = 0
        for m in users:
            try:
                await tools.commit_profile_change(self.bot, m, item, name, revoke=True)

            except (ValueError, AttributeError):
                failCount += 1

        if not failCount:
            # 0 Failures
            return await msg.edit(
                content=f'{config.greenTick} {item.title()} `{name}` revoked from {len(users)} member(s)'
            )

        elif failCount == len(users):
            return await msg.edit(content=f'{config.redTick} {item.title()} `{name}` revoked from 0 members')

        else:
            return await msg.edit(
                content=f'{config.greenTick} {item.title()} `{name}` revoked from {len(users) - failCount}/{len(users)} member(s).'
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
            for command in self.bot.tree.get_commands(guild=discord.Object(id=config.nintendoswitch)):
                # Iterate over commands in the tree so we can get the profile command ID
                if command.name == 'profile':
                    break

            commandID = command.extras['id']
            await message.channel.send(
                f'{message.author.mention} Hi! It appears you\'ve sent a **friend code**. An easy way to store and share your friend code is with our server profile system. To view your profile use the </profile view:{commandID}> command. For help on setting up your profile, including adding your friend code, use the </profile edit:{commandID}> command. You can even see the profiles of other users with `/profile view @user`'
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
