#!/bin/sh
# Sourced by dracut's initqueue/finished hook. Do not allow an incomplete root.
[ -e /run/sd-netboot-root-ready ]
