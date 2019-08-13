token = ''

mongoUser = ''
mongoPass = ''
mongoHost = '127.0.0.1'

# Channel IDs
modChannel = 605435687537344513 # Test right now
logChannel = 596723497233940483
debugChannel = 276036563866091521
adminChannel = 238081192019099650
offclockChannel = 462357369343705123
spoilers = 335447020359778305
suggestions = 238865295215689729
showModCTX = [debugChannel, adminChannel, offclockChannel]

# Role IDs
moderator = 263764663152541696
eh = 315332000032489474
warnTier1 = 278643995457093633
warnTier2 = 278644047357149185
warnTier3 = 278644075253465089
mute = 243656194340814848
noSpoilers = 587768721825857536
noSuggestions = 528359937017905155

# Emoji IDs
online = '<:online:319200223350095872>'
away = '<:away:319200276206845962>'
dnd = '<:dnd:319200300726616064>'
offline = '<:offline:319200260566286336>'
streaming = '<:streaming:469693769919234060>'
redTick = '<:redTick:402505117733224448>'
greenTick = '<:greenTick:402505080831737856>'
playButton = '<:playbutton:601597993980002304>'
nextTrack = '<:nexttrack:601597993984196618>'
fastForward = '<:fastforward:601597993988259870>'
downTriangle = '<:downwardsredtri:601597993925345293>'

# Web server
baseUrl = 'https://bowser.mattbsg.xyz/api'

# Text constants
punDM = 'You have received a moderation action on the /r/NintendoSwitch Discord server.\n' \
    'Action: **{}**\n' \
    'Reason:\n```{}```\n' \
    'Responsible moderator: {}\n' \
    'If you have questions concerning this matter, please feel free to contact the respective moderator that took this action or another member of the moderation team.\n\n' \
    'Please do not respond to this message, I cannot reply.'

punStrs = {
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
    'unblacklist': 'Unblacklist'
}
