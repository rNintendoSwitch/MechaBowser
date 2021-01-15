# type: ignore

token = 'token'
command_prefixes = ['!', ',', '.']

#Mongo Credentials
mongoUser = 'user'
mongoPass = 'password'
mongoHost = 'host'

# Users
parakarry = int: bot

# Guild IDs
nintendoswitch = int: guild

# Channel IDs
modChannel = int: modlog
publicModChannel = int: public modlog
logChannel = int: loglog 
debugChannel = int: testing 
adminChannel = int: admin 
trialModChannel = int: trialmod 
boostChannel = int: nitro booster 
offclockChannel = int: admin offtopic id
switchHelp = int: switch help 
spoilers = int: spoilers 
suggestions = int: suggestions 
voiceTextChannel = int: voice text 
smm2Channel = int: smm2 
commandsChannel = int: commands 
marioluigiChannel = int: mario and luigi 
splatoon2Channel = int: splatoon2
automodChannel = int: automod alerts

# Category IDs
eventCat = int: server events
modmailCat = int: modmail category

showModCTX = [debugChannel, adminChannel, offclockChannel, trialModChannel, modmailCat]

# Role IDs
boostRole = int: nitro boosters
chatmod = int: chat moderator
submod = int: subreddit moderator
moderator = int: moderator
modemeritus = int: moderator emeritus
submodemeritus = int: sub moderator emeritus
eh = int: test server mod
helpfulUser = int: helpful user
mute = int: timeout
noSpoilers = int: spoiler restricted
noSuggestions = int: suggestion restricted
noReactions = int: reaction restricted
noEmbeds = int: attachments and embeds restricted
noEvents = int: event restricted
voiceTextAccess = int: voice chat

# Emoji IDs
loading = '<a:loading:659107120419045403>'
online = '<:online:319200223350095872>'
away = '<:away:319200276206845962>'
dnd = '<:dnd:319200300726616064>'
offline = '<:offline:319200260566286336>'
streaming = '<:streaming:469693769919234060>'
redTick = '<:redTick:402505117733224448>'
greenTick = '<:greenTick:402505080831737856>'
barChart = '<:barchart:612724385505083392>'
playButton = '▶'
nextTrack = '⏭'
fastForward = '⏩'
downTriangle = '🔻'
stopSign = '🛑'

# Invites
banAppealInvite = str: invite to ban appeal server

# Web
baseUrl = 'https://example.com'
# Text constants
punDM = 'You have received a moderation action on the /r/NintendoSwitch Discord server.\n' \
    'Action: **{}**\n' \
    'Reason:\n```{}```\n' \
    'Responsible moderator: {}\n' \
    'If you have questions concerning this matter, please feel free to contact the respective moderator that took this action or another member of the moderation team.\n\n' \
    'Please do not respond to this message, I cannot reply.'

punStrs = {
    'strike': 'Strike',
    'destrike': 'Removed Strike',
    'tier1': 'Tier 1 Warning',
    'tier2': 'Tier 2 Warning',
    'tier3': 'Tier 3 Warning',
    'mute': 'Mute',
    'unmute': 'Unmute',
    'clear': 'Warnings reset',
    'kick': 'Kick',
    'ban': 'Ban',
    'unban': 'Unban',
    'blacklist': 'Blacklist',
    'unblacklist': 'Unblacklist',
    'note': 'Note',
    'appealdeny': 'Denied ban appeal'
}

punColors = {
    'strike': 0xFF9C8F,
    'appealdeny': 0xFF7C6B,
    'ban': 0xE93C25,
    'destrike': 0xFFCB8F,
    'kick': 0xFFBA6B,
    'unban': 0xE98E25,
    'blacklist': 0x7EBBD9,
    'mute': 0x3680A4,
    'unblacklist': 0x80E59A,
    'unmute': 0x39C05D,
}
