# Archivos Inspector

Inspector de disco en mapa 2D y ciudad 3D, al estilo WinDirStat. Explora carpetas por tamaño real, solo en **lectura** (no borra ni modifica archivos).

**Creado por Wisquimas.**

## Requisitos

- Windows
- [Python 3](https://www.python.org/) en el `PATH`

## Cómo arrancarlo

1. Descargá el release o cloná el repositorio.
2. Ejecutá `iniciar.bat`, o en la carpeta del proyecto:

```bat
python server.py
```

3. Se abre el navegador con la interfaz. Elegí una carpeta y pulsá **Analizar carpeta**.

## Qué incluye

- Mapa de bloques 2D (treemap) por tamaño
- Vista 3D navegable (órbita, cenital, caminar)
- Filtros por tipo (video, imagen, código, etc.), duplicados y antigüedad
- Vendor local de Three.js (sin CDN obligatorio)

## Licencia

Software libre bajo [MIT](LICENSE). Copyright (c) 2026 Wisquimas.
