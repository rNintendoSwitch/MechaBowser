#!/bin/bash
echo ------------------------------------------------------------------------
echo                      IMPORTANT COVERAGE INFORMATION                     
echo ------------------------------------------------------------------------
echo   You must close the bot using 'jsk shutdown' in order to generate 
echo   code coverage files. CLOSING THE BOT WITH CTRL-C WILL NOT GENERATE
echo   COVERAGE files!
echo ------------------------------------------------------------------------
coverage run bot.py
coverage report
coverage html
coverage xml
