import logging
from .AnimalGame import AnimalGame


def setup(bot):
    bot.add_cog(AnimalGame(bot))
    logging.info("[Extension] Animal Crossing Event module loaded")


def teardown(bot):
    bot.remove_cog("AnimalGame")
    logging.info("[Extension] Animal Crossing Event module unloaded")
