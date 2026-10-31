#!/bin/sh
# Sourced by dracut. The fixed Python service owns this root type.
if [ "$(getarg root=)" = sd-netboot ]; then
    root=sd-netboot
    rootok=1
    netroot=
fi
