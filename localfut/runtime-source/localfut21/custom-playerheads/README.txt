LocalFUT custom per-card player portraits
=========================================

These files override ONLY the matching special-card resource ID.

Neymar Jr 94 Flashback
  Resource ID: 50522519
  File name:   p50522519.dds

Iniesta 90 Flashback
  Resource ID: 83886121
  File name:   p83886121.dds

Format:
  DDS. BC3 / DXT5 is recommended, with transparency preserved.
  Keep the source portrait cropped/prepared the way you want it to appear.

The normal Neymar (assetId 190871) and normal Iniesta (assetId 41) are not
changed because this override is keyed by the custom card's resource ID.

After replacing a portrait:
  - fully restart FIFA 21 and LocalFUT.
  - check the LocalFUT log for:
      Serving custom player head resource=<id> file=p<id>.dds