FIFA 21 PNG -> DDS drag-and-drop converter

Files:
  PNG-to-FIFA21-DDS.ps1
  Drag-PNG-Here.bat

Requires:
  texconv.exe from Microsoft's DirectXTex project.

Setup:
  Put texconv.exe in the same folder as these two files.

Usage:
  1. Drag a transparent PNG onto Drag-PNG-Here.bat.
  2. Enter the custom card Resource ID when prompted.
     Example:
       50527512
  3. The output will be:
       p50527512.dds

The converter uses:
  180x180
  BC3_UNORM / DXT5
  1 mip level

For LocalFUT, place the resulting DDS in:
  localfut\runtime-source\localfut21\custom-playerheads\
