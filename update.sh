#!/bin/bash
# Script that updates all submodules, using a GitHub PAT to update private modules if defined

echo 'Updating root...'
git checkout master
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
