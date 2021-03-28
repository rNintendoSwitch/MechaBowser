#!/bin/bash
# Updates submodules, but allow for env vars for private to use tokens for pull

echo 'Updating root...'
git pull

echo 'Updating twemoji...'
git submodule update --remote resources/twemoji

echo 'Updating private...'
if [[ -z "${GITHUB_TOKEN}" ]]; then
    git submodule update --remote private
else
    cd private
    git pull https://${GITHUB_USER:?}:${GITHUB_TOKEN}@github.com/rNintendoSwitch/MechaBowser-Private-Modules.git
    cd ..
fi