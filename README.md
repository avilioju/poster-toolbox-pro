# POSTER TOOLBOX PRO

Herramienta unificada para buscar, descargar y organizar pósteres desde una sola aplicación en Python.

## Funciones

- Buscar pósteres por tema mediante DDGS.
- Filtrar por orientación, resolución y proporción real.
- Descargar todas las variantes disponibles de una película desde IMPAwards.
- Crear un PDF conservando los JPG originales sin recompresión.
- Evitar imágenes duplicadas y registrar las fuentes de las búsquedas generales.
- Compatible con Windows, Linux y macOS.

## Uso en Windows

1. Descarga `POSTER_TOOLBOX_PRO.py`.
2. Ábrelo con Python 3.10 o superior.
3. Elige una herramienta en el menú.

El programa **no crea ni activa un VENV en Windows**. Al iniciar comprueba estos componentes e instala únicamente los que falten usando el Python actual:

- requests
- ddgs
- Pillow
- beautifulsoup4
- img2pdf
- tqdm

## Menú

1. Buscar pósteres por tema.
2. Descargar variantes de IMPAwards y crear PDF.
3. Comprobar los requerimientos instalados.
0. Salir.

## Uso desde la terminal

```bash
python POSTER_TOOLBOX_PRO.py
python POSTER_TOOLBOX_PRO.py buscar "Harry Potter" -o vertical -n 20
python POSTER_TOOLBOX_PRO.py impawards ENLACE
python POSTER_TOOLBOX_PRO.py --check
```

## Carpetas generadas

- `POSTERS_DESCARGADOS`: búsquedas generales.
- `IMPAWARDS_DESCARGADOS`: imágenes y PDF de IMPAwards.

## Aviso

Las imágenes pueden estar protegidas por derechos de autor. Revisa la licencia y las condiciones de la fuente antes de utilizarlas comercialmente.
