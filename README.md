# RandPad

Virtual Xbox-style gamepad for Linux that either passes your real controller
through or sends random inputs. Toggle with a button, a pad combo, or F9.

## Install (apt, gets updates automatically)
Download the latest `.deb` from Releases, then:

    sudo apt install ./randpad_*_all.deb
    sudo apt update && sudo apt upgrade     # future updates

## Release a new version
    git commit -am "describe the change"
    git tag v1.0.1
    git push && git push --tags

The `release` workflow builds the .deb, attaches it to a GitHub Release, and
republishes the apt repo on GitHub Pages.

## Build locally
    sh build_deb.sh 1.0.1
