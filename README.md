# Archivos Inspector

Inspector de disco en mapa 2D y ciudad 3D, al estilo WinDirStat. Explora carpetas por tamaño real, solo en **lectura** (no borra ni modifica archivos).

**Creado por Wisquimas.** Licencia MIT.

## Descargar instalador (recomendado)

No hace falta tener Python instalado.

1. Entrá a [Releases](https://github.com/wisquimas/ArchivosInspector/releases) y descargá **`ArchivosInspector-Setup-1.1.0.exe`**.
2. Ejecutá el instalador (podés elegir la carpeta; por defecto `Program Files`).
3. Abrí **Archivos Inspector** desde el menú Inicio (o el acceso directo del escritorio si lo marcaste).
4. Se abre el navegador en `http://127.0.0.1:…` con la interfaz.

También hay un zip portable: **`ArchivosInspector-Portable-1.1.0.zip`** (descomprimí y ejecutá `ArchivosInspector.exe`).

## Correr desde código

Si preferís el código fuente:

- Windows
- [Python 3](https://www.python.org/) en el `PATH`

```bat
iniciar.bat
```

o:

```bat
python server.py
```

## Qué incluye

- Mapa de bloques 2D (treemap) por tamaño
- Vista 3D navegable (órbita, cenital, caminar)
- Filtros por tipo (video, imagen, código, etc.), duplicados y antigüedad
- Vendor local de Three.js (sin CDN obligatorio)
- Servidor local de solo lectura en `127.0.0.1`

## Compilar el instalador (desarrolladores)

En Windows, con Python 3 y PyInstaller:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
powershell -ExecutionPolicy Bypass -File scripts\build_installer.ps1
```

El segundo script usa [Inno Setup](https://jrsoftware.org/isinfo.php) (`winget install JRSoftware.InnoSetup` si falta). Salida en `release\`:

- `ArchivosInspector-Setup-1.1.0.exe` — instalador
- `ArchivosInspector-Portable-1.1.0.zip` — carpeta empaquetada

`dist\` y `release\` no se versionan en git; los binarios van en el release de GitHub.

## Licencia

Software libre bajo [MIT](LICENSE). Copyright (c) 2026 Wisquimas.
