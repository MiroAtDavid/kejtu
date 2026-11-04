The native app icon is generated from the neutral Kejtu vector in `icon.svg`.
It contains no profile picture or local metadata. Regenerate the Linux PNG with:

```sh
rsvg-convert --width 512 --height 512 --output icon.png icon.svg
```

The Windows ICO contains only resized copies of that existing PNG (256, 128,
64, 48, 32 and 16 pixels). Regenerate it mechanically with ImageMagick:

```sh
magick icon.png -define icon:auto-resize=256,128,64,48,32,16 icon.ico
```
