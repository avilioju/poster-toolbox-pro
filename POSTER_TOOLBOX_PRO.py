#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
POSTER TOOLBOX PRO
==================

Caja de herramientas unificada para buscar y descargar pósteres.

Herramientas incluidas:
  1. Buscador general por tema con DDGS.
  2. Descargador de todas las variantes de una película desde IMPAwards.
  3. Creación de un PDF sin recomprimir los JPG descargados de IMPAwards.

Características principales:
  - Compatible con Windows, Linux y macOS.
  - En Windows NO crea ni utiliza un VENV automáticamente.
  - Comprueba los módulos e instala solamente los que falten con el Python actual.
  - Menú interactivo y ejecución opcional mediante parámetros.
  - Descarga concurrente, con reintentos y límites de tamaño.
  - Comprueba el formato, las dimensiones y la proporción reales de cada archivo.
  - Reemplaza automáticamente descargas fallidas hasta alcanzar la cantidad pedida.
  - Evita URL repetidas, archivos duplicados y sobrescrituras.
  - Crea FUENTES.csv con el origen de cada imagen.

Uso normal:
  Doble clic en Windows o: python POSTER_TOOLBOX_PRO.py

Ejemplos directos:
  python POSTER_TOOLBOX_PRO.py buscar "Harry Potter" -o vertical -n 20
  python POSTER_TOOLBOX_PRO.py impawards https://www.impawards.com/2026/pelicula.html
  python POSTER_TOOLBOX_PRO.py --check

Ayuda completa:
  python BAJAR_POSTERS_PRO_UNIFICADO.py --help

Las imágenes encontradas pueden estar protegidas por derechos de autor. Revisa la
licencia de la página de origen antes de utilizarlas comercialmente.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys


VERSION = "4.0"
PAQUETES = (
    ("requests", "requests>=2.32,<3"),
    ("ddgs", "ddgs>=9.6,<10"),
    ("PIL", "Pillow>=10,<13"),
    ("bs4", "beautifulsoup4>=4.12,<5"),
    ("img2pdf", "img2pdf>=0.5,<1"),
    ("tqdm", "tqdm>=4.66,<5"),
)


def preparar_consola() -> None:
    """Evita errores al mostrar tildes y emojis en consolas de Windows."""
    for flujo in (sys.stdout, sys.stderr):
        reconfigure = getattr(flujo, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def dependencias_faltantes() -> list[str]:
    return [paquete for modulo, paquete in PAQUETES if importlib.util.find_spec(modulo) is None]


def instalar_dependencias() -> None:
    """Instala los módulos ausentes con el Python actual, sin crear un VENV."""
    faltantes = dependencias_faltantes()
    if not faltantes:
        return

    if os.environ.get("POSTER_TOOLBOX_BOOTSTRAP") == "1":
        nombres = ", ".join(faltantes)
        raise RuntimeError(f"No se pudieron importar estas dependencias: {nombres}")

    try:
        prueba_pip = subprocess.run(
            [sys.executable, "-m", "pip", "--version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if prueba_pip.returncode != 0:
            print("\n🔧 Preparando pip para este Python...")
            subprocess.run(
                [sys.executable, "-m", "ensurepip", "--upgrade"],
                check=True,
            )

        print("\n📦 Faltan algunos componentes:")
        for paquete in faltantes:
            print(f"   • {paquete}")
        print("📦 Instalándolos con el Python actual (sin VENV)...")

        comando = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            *faltantes,
        ]
        resultado = subprocess.run(comando, check=False)
        if resultado.returncode != 0:
            print("   ⚠️ Reintentando la instalación solo para este usuario...")
            subprocess.run([*comando[:5], "--user", *comando[5:]], check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        comando_manual = " ".join(faltantes)
        raise RuntimeError(
            "No se pudieron instalar las dependencias. Ejecuta este comando manualmente:\n"
            f'"{sys.executable}" -m pip install {comando_manual}'
        ) from exc

    importlib.invalidate_caches()
    print("🚀 Componentes instalados. Reiniciando POSTER TOOLBOX PRO...\n")
    entorno = os.environ.copy()
    entorno["POSTER_TOOLBOX_BOOTSTRAP"] = "1"
    codigo = subprocess.call(
        [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]],
        env=entorno,
    )
    raise SystemExit(codigo)


preparar_consola()
try:
    instalar_dependencias()
except RuntimeError as error_bootstrap:
    print(f"\n❌ {error_bootstrap}")
    sys.exit(1)


import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from dataclasses import dataclass
from datetime import datetime
import hashlib
import re
import shutil
import tempfile
import threading
import time
from typing import Any
from urllib.parse import urljoin, urlparse, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from ddgs import DDGS
import img2pdf
from PIL import Image, UnidentifiedImageError
import requests
from tqdm import tqdm


Image.MAX_IMAGE_PIXELS = 120_000_000

PROPORCIONES: dict[str, tuple[float, ...]] = {
    "vertical": (2 / 3, 9 / 16),
    "horizontal": (3 / 2, 16 / 9),
    "cuadrado": (1.0,),
}
MODOS = {
    "1": "vertical",
    "2": "horizontal",
    "3": "cuadrado",
    "4": "ambas",
    "5": "todo",
}
EXTENSIONES = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
    "BMP": ".bmp",
    "TIFF": ".tif",
}
LICENCIAS = {
    "sin_filtro": None,
    "dominio_publico": "Public",
    "compartir": "Share",
    "comercial": "ShareCommercially",
    "modificar": "Modify",
    "modificar_comercial": "ModifyCommercially",
}
MAXIMO_BYTES = 50 * 1024 * 1024
TOLERANCIA_PROPORCION = 0.065
HILO_LOCAL = threading.local()


@dataclass(slots=True)
class DescargaValida:
    temporal: Path
    ancho: int
    alto: int
    formato: str
    extension: str
    orientacion: str
    huella: str
    titulo: str
    pagina_origen: str
    imagen_url: str
    consulta: str


def limpiar_nombre(texto: str, maximo: int = 90) -> str:
    """Genera un nombre válido en Windows, Linux y macOS."""
    limpio = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", texto)
    limpio = re.sub(r"\s+", " ", limpio).strip(" ._")
    limpio = limpio[:maximo].rstrip(" ._") or "POSTERS"

    reservados = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
    if limpio.upper() in reservados:
        limpio = f"_{limpio}"
    return limpio


def normalizar_url(url: str) -> str:
    try:
        partes = urlsplit(url.strip())
        return urlunsplit((partes.scheme.lower(), partes.netloc.lower(), partes.path, partes.query, ""))
    except ValueError:
        return url.strip()


def orientacion_real(ancho: int, alto: int) -> str:
    diferencia = abs(ancho - alto) / max(ancho, alto)
    if diferencia <= TOLERANCIA_PROPORCION:
        return "cuadrado"
    return "vertical" if alto > ancho else "horizontal"


def proporcion_valida(ancho: int, alto: int, modo: str) -> bool:
    if ancho <= 0 or alto <= 0:
        return False

    orientaciones: tuple[str, ...]
    if modo == "ambas":
        orientaciones = ("vertical", "horizontal")
    elif modo == "todo":
        orientaciones = ("vertical", "horizontal", "cuadrado")
    else:
        orientaciones = (modo,)

    proporcion = ancho / alto
    return any(
        abs(proporcion - objetivo) / objetivo <= TOLERANCIA_PROPORCION
        for orientacion in orientaciones
        for objetivo in PROPORCIONES[orientacion]
    )


def consultas_para(tema: str, modo: str) -> list[str]:
    comunes = [tema, f"{tema} artwork high resolution"]
    verticales = [
        f"{tema} poster",
        f"{tema} official poster",
        f"{tema} vertical poster 2:3",
        f"{tema} portrait wallpaper 9:16",
    ]
    horizontales = [
        f"{tema} wallpaper 4k",
        f"{tema} widescreen wallpaper 16:9",
        f"{tema} landscape artwork",
        f"{tema} cinematic wallpaper",
    ]
    cuadrados = [
        f"{tema} square artwork",
        f"{tema} album cover 1:1",
        f"{tema} square poster high resolution",
    ]

    if modo == "vertical":
        consultas = comunes + verticales
    elif modo == "horizontal":
        consultas = comunes + horizontales
    elif modo == "cuadrado":
        consultas = comunes + cuadrados
    elif modo == "ambas":
        consultas = comunes + verticales + horizontales
    else:
        consultas = comunes + verticales + horizontales + cuadrados

    return list(dict.fromkeys(consultas))


def layout_para(modo: str) -> str | None:
    return {
        "vertical": "Tall",
        "horizontal": "Wide",
        "cuadrado": "Square",
    }.get(modo)


def url_de_imagen(resultado: dict[str, Any]) -> str:
    """No usa `url`: en DDGS ese campo corresponde a la página de origen."""
    for clave in ("image", "contentUrl", "image_url", "full"):
        valor = resultado.get(clave)
        if isinstance(valor, str) and valor.startswith(("http://", "https://")):
            return valor
    return ""


def buscar_candidatos(
    tema: str,
    modo: str,
    cantidad: int,
    region: str,
    safesearch: str,
    licencia: str,
) -> list[dict[str, Any]]:
    """Obtiene más candidatos de los necesarios para poder reemplazar fallos."""
    objetivo = min(max(cantidad * 7, 80), 500)
    por_consulta = min(max(cantidad * 3, 50), 150)
    candidatos: list[dict[str, Any]] = []
    urls_usadas: set[str] = set()
    consultas = consultas_para(tema, modo)
    layout = layout_para(modo)
    filtro_licencia = LICENCIAS[licencia]

    print(f"\n🔎 Buscando candidatos (objetivo: {objetivo})...")
    buscador = DDGS(timeout=15)

    for numero, consulta in enumerate(consultas, start=1):
        if len(candidatos) >= objetivo:
            break
        print(f"   [{numero}/{len(consultas)}] {consulta}")

        resultados: list[dict[str, Any]] = []
        for intento in range(1, 3):
            try:
                argumentos: dict[str, Any] = {
                    "query": consulta,
                    "region": region,
                    "safesearch": safesearch,
                    "max_results": por_consulta,
                    "backend": "auto",
                    "size": "Large",
                }
                if layout:
                    argumentos["layout"] = layout
                if filtro_licencia:
                    argumentos["license_image"] = filtro_licencia
                resultados = list(buscador.images(**argumentos))
                break
            except Exception as exc:  # DDGS cambia sus excepciones entre versiones
                if intento == 2:
                    print(f"      ⚠️ No respondió: {type(exc).__name__}")
                else:
                    time.sleep(1.5)

        for resultado in resultados:
            imagen_url = url_de_imagen(resultado)
            normalizada = normalizar_url(imagen_url)
            if not normalizada or normalizada in urls_usadas:
                continue
            urls_usadas.add(normalizada)
            copia = dict(resultado)
            copia["_imagen_url"] = imagen_url
            copia["_consulta"] = consulta
            candidatos.append(copia)
            if len(candidatos) >= objetivo:
                break

        time.sleep(0.25)

    print(f"   ✅ {len(candidatos)} URL únicas encontradas.")
    return candidatos


def sesion_http() -> requests.Session:
    sesion = getattr(HILO_LOCAL, "sesion", None)
    if sesion is None:
        sesion = requests.Session()
        sesion.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0 Safari/537.36"
                ),
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                "Accept-Language": "es-ES,es;q=0.9,en;q=0.7",
            }
        )
        HILO_LOCAL.sesion = sesion
    return sesion


def borrar_si_existe(ruta: Path) -> None:
    try:
        ruta.unlink(missing_ok=True)
    except OSError:
        pass


def descargar_y_validar(
    candidato: dict[str, Any],
    temporal_dir: Path,
    modo: str,
    min_ancho: int,
) -> tuple[DescargaValida | None, str]:
    imagen_url = str(candidato.get("_imagen_url", ""))
    pagina_origen = str(candidato.get("url") or candidato.get("source") or "")
    titulo = str(candidato.get("title") or "Sin título")
    consulta = str(candidato.get("_consulta") or "")
    fd, nombre_temporal = tempfile.mkstemp(prefix="imagen_", suffix=".part", dir=temporal_dir)
    os.close(fd)
    temporal = Path(nombre_temporal)

    ultimo_error = "error desconocido"
    for intento in range(1, 3):
        borrar_si_existe(temporal)
        try:
            cabeceras: dict[str, str] = {}
            if pagina_origen.startswith(("http://", "https://")):
                cabeceras["Referer"] = pagina_origen

            with sesion_http().get(
                imagen_url,
                headers=cabeceras,
                timeout=(8, 30),
                stream=True,
                allow_redirects=True,
            ) as respuesta:
                respuesta.raise_for_status()
                tipo = respuesta.headers.get("Content-Type", "").lower()
                if any(no_imagen in tipo for no_imagen in ("text/html", "application/json", "text/plain")):
                    return None, "el servidor devolvió una página, no una imagen"

                anunciado = respuesta.headers.get("Content-Length")
                if anunciado and int(anunciado) > MAXIMO_BYTES:
                    return None, "archivo mayor de 50 MB"

                total = 0
                huella = hashlib.sha256()
                with temporal.open("wb") as archivo:
                    for bloque in respuesta.iter_content(chunk_size=64 * 1024):
                        if not bloque:
                            continue
                        total += len(bloque)
                        if total > MAXIMO_BYTES:
                            raise ValueError("archivo mayor de 50 MB")
                        huella.update(bloque)
                        archivo.write(bloque)

            if total < 1024:
                return None, "archivo vacío o demasiado pequeño"

            with Image.open(temporal) as imagen:
                ancho, alto = imagen.size
                formato = (imagen.format or "").upper()
                imagen.verify()

            if formato not in EXTENSIONES:
                return None, f"formato no admitido ({formato or 'desconocido'})"
            if ancho < min_ancho:
                return None, f"ancho insuficiente ({ancho}px)"
            if not proporcion_valida(ancho, alto, modo):
                return None, f"proporción no válida ({ancho}x{alto})"

            return (
                DescargaValida(
                    temporal=temporal,
                    ancho=ancho,
                    alto=alto,
                    formato=formato,
                    extension=EXTENSIONES[formato],
                    orientacion=orientacion_real(ancho, alto),
                    huella=huella.hexdigest(),
                    titulo=titulo,
                    pagina_origen=pagina_origen,
                    imagen_url=imagen_url,
                    consulta=consulta,
                ),
                "",
            )
        except (
            requests.RequestException,
            OSError,
            ValueError,
            UnidentifiedImageError,
            Image.DecompressionBombError,
        ) as exc:
            ultimo_error = str(exc).strip() or type(exc).__name__
            if intento < 2:
                time.sleep(0.8)
        except Exception as exc:
            ultimo_error = f"{type(exc).__name__}: {exc}"
            break

    borrar_si_existe(temporal)
    return None, ultimo_error


def huella_archivo(ruta: Path) -> str:
    calculo = hashlib.sha256()
    with ruta.open("rb") as archivo:
        for bloque in iter(lambda: archivo.read(1024 * 1024), b""):
            calculo.update(bloque)
    return calculo.hexdigest()


def estado_existente(carpeta: Path) -> tuple[set[str], int]:
    huellas: set[str] = set()
    mayor_numero = 0
    patron = re.compile(r"^(\d+)_")
    extensiones = set(EXTENSIONES.values())

    for ruta in carpeta.iterdir():
        if not ruta.is_file() or ruta.suffix.lower() not in extensiones:
            continue
        coincidencia = patron.match(ruta.name)
        if coincidencia:
            mayor_numero = max(mayor_numero, int(coincidencia.group(1)))
        try:
            huellas.add(huella_archivo(ruta))
        except OSError:
            pass
    return huellas, mayor_numero + 1


def registrar_fuente(ruta_csv: Path, archivo: str, descarga: DescargaValida) -> None:
    campos = (
        "archivo",
        "ancho",
        "alto",
        "orientacion",
        "titulo",
        "pagina_origen",
        "imagen_url",
        "consulta",
        "fecha_descarga",
    )
    nuevo = not ruta_csv.exists() or ruta_csv.stat().st_size == 0
    with ruta_csv.open("a", encoding="utf-8-sig", newline="") as salida:
        escritor = csv.DictWriter(salida, fieldnames=campos)
        if nuevo:
            escritor.writeheader()
        escritor.writerow(
            {
                "archivo": archivo,
                "ancho": descarga.ancho,
                "alto": descarga.alto,
                "orientacion": descarga.orientacion,
                "titulo": descarga.titulo,
                "pagina_origen": descarga.pagina_origen,
                "imagen_url": descarga.imagen_url,
                "consulta": descarga.consulta,
                "fecha_descarga": datetime.now().astimezone().isoformat(timespec="seconds"),
            }
        )


def guardar_descarga(descarga: DescargaValida, carpeta: Path, numero: int) -> Path:
    while True:
        nombre = (
            f"{numero:03d}_{descarga.ancho}x{descarga.alto}_"
            f"{descarga.orientacion}{descarga.extension}"
        )
        destino = carpeta / nombre
        if not destino.exists():
            shutil.move(str(descarga.temporal), str(destino))
            return destino
        numero += 1


def descargar_candidatos(
    candidatos: list[dict[str, Any]],
    carpeta: Path,
    modo: str,
    cantidad: int,
    min_ancho: int,
    hilos: int,
    guardar_fuentes: bool,
) -> int:
    huellas, siguiente_numero = estado_existente(carpeta)
    aceptadas = 0
    revisadas = 0
    motivos: Counter[str] = Counter()
    ruta_csv = carpeta / "FUENTES.csv"
    tamano_lote = max(hilos * 3, 6)

    print(f"\n📥 Descargando y comprobando hasta obtener {cantidad} imágenes válidas...")
    if huellas:
        print(f"   ℹ️ Se conservarán {len(huellas)} imágenes existentes; la numeración continuará.")

    with tempfile.TemporaryDirectory(prefix=".bajarposters_", dir=carpeta) as temporal:
        temporal_dir = Path(temporal)
        with ThreadPoolExecutor(max_workers=hilos, thread_name_prefix="poster") as ejecutor:
            for inicio in range(0, len(candidatos), tamano_lote):
                if aceptadas >= cantidad:
                    break

                lote = candidatos[inicio : inicio + tamano_lote]
                futuros = {
                    ejecutor.submit(
                        descargar_y_validar,
                        candidato,
                        temporal_dir,
                        modo,
                        min_ancho,
                    ): candidato
                    for candidato in lote
                }

                for futuro in as_completed(futuros):
                    revisadas += 1
                    try:
                        descarga, motivo = futuro.result()
                    except Exception as exc:
                        descarga, motivo = None, f"fallo interno: {type(exc).__name__}"

                    if descarga is None:
                        motivos[motivo or "descarga rechazada"] += 1
                        continue

                    if descarga.huella in huellas:
                        borrar_si_existe(descarga.temporal)
                        motivos["imagen duplicada"] += 1
                        continue

                    if aceptadas >= cantidad:
                        borrar_si_existe(descarga.temporal)
                        continue

                    destino = guardar_descarga(descarga, carpeta, siguiente_numero)
                    siguiente_numero = int(destino.name.split("_", 1)[0]) + 1
                    huellas.add(descarga.huella)
                    aceptadas += 1
                    if guardar_fuentes:
                        registrar_fuente(ruta_csv, destino.name, descarga)
                    print(
                        f"   ✅ {aceptadas:>3}/{cantidad}  "
                        f"{descarga.ancho}x{descarga.alto}  {destino.name}"
                    )

                porcentaje = min(100, round(aceptadas * 100 / cantidad))
                print(
                    f"      Progreso real: {porcentaje}% | "
                    f"revisadas: {revisadas}/{len(candidatos)}"
                )

    if aceptadas < cantidad:
        print(f"\n⚠️ Solo se consiguieron {aceptadas} de {cantidad} imágenes válidas.")
    if motivos:
        principales = motivos.most_common(5)
        print(
            "   Rechazos principales: "
            + "; ".join(f"{motivo} ({total})" for motivo, total in principales)
        )
    return aceptadas


def pedir_entero(mensaje: str, predeterminado: int, minimo: int, maximo: int) -> int:
    while True:
        respuesta = input(f"{mensaje} [{predeterminado}]: ").strip()
        if not respuesta:
            return predeterminado
        try:
            valor = int(respuesta)
            if minimo <= valor <= maximo:
                return valor
        except ValueError:
            pass
        print(f"   ❌ Escribe un número entre {minimo} y {maximo}.")


def mostrar_ayuda_interactiva() -> None:
    print(
        """
Ejemplos de temas:
  • Harry Potter
  • TWICE What is Love
  • Studio Ghibli
  • paisaje del Perú

Orientaciones admitidas:
  1. Vertical:   2:3 y 9:16
  2. Horizontal: 3:2 y 16:9
  3. Cuadrado:   1:1
  4. Ambas:      vertical + horizontal
  5. Todo:       vertical + horizontal + cuadrado
"""
    )


def completar_interactivo(args: argparse.Namespace) -> argparse.Namespace:
    print("=" * 68)
    print(f" BAJAR POSTERS {VERSION} - imágenes verificadas en alta resolución")
    print("=" * 68)

    while not args.tema:
        respuesta = input("\n🎯 Tema (o escribe 'ayuda'): ").strip()
        if respuesta.lower() == "ayuda":
            mostrar_ayuda_interactiva()
        elif respuesta:
            args.tema = respuesta

    if not args.orientacion:
        print("\n📐 ORIENTACIÓN:")
        print("  1. Vertical   (2:3 y 9:16)")
        print("  2. Horizontal (3:2 y 16:9)")
        print("  3. Cuadrado   (1:1)")
        print("  4. Ambas      (vertical + horizontal)")
        print("  5. Todo       (incluye cuadrado)")
        while True:
            opcion = input("Elige una opción (1-5): ").strip()
            if opcion in MODOS:
                args.orientacion = MODOS[opcion]
                break
            print("   ❌ Elige 1, 2, 3, 4 o 5.")

    if args.cantidad is None:
        args.cantidad = pedir_entero("\n📸 Cantidad de imágenes", 30, 1, 100)
    if args.min_ancho is None:
        args.min_ancho = pedir_entero("📏 Ancho mínimo en píxeles", 1000, 300, 8000)
    return args


def crear_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Descarga pósteres y wallpapers, comprueba sus dimensiones "
            "y evita duplicados."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("tema", nargs="?", help="tema que se desea buscar")
    parser.add_argument(
        "-o",
        "--orientacion",
        choices=("vertical", "horizontal", "cuadrado", "ambas", "todo"),
        help="proporciones aceptadas",
    )
    parser.add_argument("-n", "--cantidad", type=int, help="cantidad de imágenes nuevas")
    parser.add_argument("--min-ancho", type=int, help="ancho mínimo real en píxeles")
    parser.add_argument("--salida", type=Path, help="carpeta base de salida")
    parser.add_argument("--region", default="es-es", help="región de búsqueda de DDGS")
    parser.add_argument(
        "--busqueda-segura",
        choices=("on", "moderate", "off"),
        default="moderate",
        help="nivel de búsqueda segura",
    )
    parser.add_argument(
        "--licencia",
        choices=tuple(LICENCIAS),
        default="sin_filtro",
        help="filtro de licencia solicitado al buscador",
    )
    parser.add_argument("--hilos", type=int, default=4, help="descargas simultáneas (1-8)")
    parser.add_argument("--sin-fuentes", action="store_true", help="no crear FUENTES.csv")
    parser.add_argument("--no-abrir", action="store_true", help="no preguntar si se abre la carpeta")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def validar_argumentos(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.cantidad is not None and not 1 <= args.cantidad <= 100:
        parser.error("--cantidad debe estar entre 1 y 100")
    if args.min_ancho is not None and not 300 <= args.min_ancho <= 8000:
        parser.error("--min-ancho debe estar entre 300 y 8000")
    if not 1 <= args.hilos <= 8:
        parser.error("--hilos debe estar entre 1 y 8")


def abrir_carpeta(carpeta: Path) -> bool:
    try:
        if os.name == "nt":
            os.startfile(str(carpeta))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(carpeta)])
        else:
            subprocess.Popen(
                ["xdg-open", str(carpeta)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def ejecutar_busqueda_general(argv: list[str] | None = None) -> int:
    parser = crear_parser()
    args = parser.parse_args(argv)
    validar_argumentos(parser, args)
    interactivo = args.tema is None
    args = completar_interactivo(args)

    if not args.orientacion:
        args.orientacion = "vertical"
    if args.cantidad is None:
        args.cantidad = 30
    if args.min_ancho is None:
        args.min_ancho = 1000

    carpeta_base = (
        args.salida.expanduser()
        if args.salida
        else Path(__file__).resolve().parent / "POSTERS_DESCARGADOS"
    )
    carpeta = carpeta_base.resolve() / f"{limpiar_nombre(args.tema)}_{args.orientacion}"
    try:
        carpeta.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"\n❌ No se pudo crear la carpeta de salida: {exc}")
        return 1

    print("\n⚙️ CONFIGURACIÓN")
    print(f"   Tema:        {args.tema}")
    print(f"   Orientación: {args.orientacion}")
    print(f"   Cantidad:    {args.cantidad}")
    print(f"   Ancho mín.:  {args.min_ancho}px")
    print(f"   Carpeta:     {carpeta}")

    candidatos = buscar_candidatos(
        tema=args.tema,
        modo=args.orientacion,
        cantidad=args.cantidad,
        region=args.region,
        safesearch=args.busqueda_segura,
        licencia=args.licencia,
    )
    if not candidatos:
        print("\n❌ No se encontraron candidatos. Revisa Internet o prueba un tema más general.")
        return 1

    total = descargar_candidatos(
        candidatos=candidatos,
        carpeta=carpeta,
        modo=args.orientacion,
        cantidad=args.cantidad,
        min_ancho=args.min_ancho,
        hilos=args.hilos,
        guardar_fuentes=not args.sin_fuentes,
    )

    print("\n" + "=" * 68)
    if total:
        print("✅ PROCESO TERMINADO")
    else:
        print("❌ NO SE PUDO DESCARGAR NINGUNA IMAGEN VÁLIDA")
    print(f"   Imágenes nuevas: {total}/{args.cantidad}")
    print(f"   Carpeta: {carpeta}")
    if not args.sin_fuentes and total:
        print("   Procedencia: FUENTES.csv")
    print("   Recuerda revisar los derechos de uso en la página de origen.")
    print("=" * 68)

    if interactivo and total and not args.no_abrir:
        respuesta = input("\n📂 ¿Abrir la carpeta ahora? [S/n]: ").strip().lower()
        if respuesta in ("", "s", "si", "sí") and not abrir_carpeta(carpeta):
            print("   ⚠️ No se pudo abrir automáticamente; usa la ruta mostrada arriba.")
    return 0 if total else 1


IMP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
    )
}
IMP_MAX_WORKERS = 8
IMP_REQUEST_TIMEOUT = 30
IMP_PRINT_LOCK = threading.Lock()


def imp_safe_print(texto: object) -> None:
    with IMP_PRINT_LOCK:
        tqdm.write(str(texto))


def imp_get_html(url: str) -> str | None:
    try:
        respuesta = requests.get(
            url,
            headers=IMP_HEADERS,
            timeout=IMP_REQUEST_TIMEOUT,
            allow_redirects=True,
        )
        if respuesta.status_code == 200:
            return respuesta.text
        imp_safe_print(f"⚠️ No se pudo abrir: {url} | Estado: {respuesta.status_code}")
    except requests.RequestException as exc:
        imp_safe_print(f"⚠️ Error abriendo {url}: {exc}")
    return None


def imp_detectar_year_y_base(url: str) -> tuple[str, str]:
    partes = urlparse(url).path.strip("/").split("/")
    if len(partes) < 2 or not partes[-1].lower().endswith(".html"):
        raise ValueError("El enlace no parece ser una página válida de IMPAwards.")

    year = partes[0]
    archivo = re.sub(r"\.html$", "", partes[-1], flags=re.IGNORECASE)
    archivo = re.sub(r"_(?:xxlg|xlg|lg)$", "", archivo, flags=re.IGNORECASE)
    base = re.sub(r"_ver\d+$", "", archivo, flags=re.IGNORECASE)
    return year, base


def imp_ordenar_variantes(url: str) -> int:
    coincidencia = re.search(r"_ver(\d+)\.html$", urlparse(url).path, re.IGNORECASE)
    return int(coincidencia.group(1)) if coincidencia else 1


def imp_extraer_nombre(html: str, base: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    texto = soup.get_text(" ", strip=True)
    patrones = (
        r"IMP Awards\s*/\s*\d{4}\s+Movie Poster Gallery\s*/\s*(.+? Poster)\s*\(#\d+\s+of\s+\d+\)",
        r"IMP Awards\s*/\s*\d{4}\s+Movie Poster Gallery\s*/\s*(.+? Poster)",
        r"(.+? Poster)\s*\(#\d+\s+of\s+\d+\)",
    )
    for patron in patrones:
        coincidencia = re.search(patron, texto, flags=re.IGNORECASE)
        if coincidencia:
            return limpiar_nombre(coincidencia.group(1), maximo=110)

    if soup.title and soup.title.string:
        titulo = soup.title.string.strip()
        titulo = re.sub(r"\s*-\s*IMP Awards.*$", "", titulo, flags=re.IGNORECASE)
        titulo = re.sub(r"\s*\(#\d+\s+of\s+\d+\).*$", "", titulo, flags=re.IGNORECASE)
        if "poster" not in titulo.lower():
            titulo += " Poster"
        return limpiar_nombre(titulo, maximo=110)
    return limpiar_nombre(base.replace("_", " ").title() + " Poster", maximo=110)


def imp_buscar_variantes(url_inicial: str, year: str, base: str, html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    variantes: set[str] = {url_inicial}
    patron = re.compile(rf"^{re.escape(base)}(_ver\d+)?\.html$", re.IGNORECASE)

    for enlace in soup.find_all("a", href=True):
        href = str(enlace["href"]).strip()
        archivo = os.path.basename(urlparse(href).path)
        if patron.match(archivo):
            variantes.add(urljoin(url_inicial, href))

    variantes.add(f"https://www.impawards.com/{year}/{base}.html")
    return sorted(variantes, key=imp_ordenar_variantes)


def imp_extraer_tamano(texto: str) -> tuple[int, int] | None:
    coincidencia = re.search(r"(\d{3,5})\s*x\s*(\d{3,5})", texto.lower())
    if not coincidencia:
        return None
    return int(coincidencia.group(1)), int(coincidencia.group(2))


def imp_buscar_paginas_de_tamano(pagina_url: str) -> list[dict[str, Any]]:
    html = imp_get_html(pagina_url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    paginas: dict[str, dict[str, Any]] = {
        pagina_url: {"url": pagina_url, "area": 0, "origen": "página original"}
    }
    for enlace in soup.find_all("a", href=True):
        href = str(enlace["href"]).strip()
        texto = enlace.get_text(" ", strip=True)
        if not href.lower().endswith(".html"):
            continue

        href_lower = href.lower()
        tamano = imp_extraer_tamano(texto)
        if not any(marca in href_lower for marca in ("_xxlg", "_xlg", "_lg")) and not tamano:
            continue

        ancho, alto = tamano or (0, 0)
        url_completa = urljoin(pagina_url, href)
        paginas[url_completa] = {
            "url": url_completa,
            "area": ancho * alto,
            "origen": texto,
        }
    return list(paginas.values())


def imp_buscar_imagen_en_pagina(pagina_url: str) -> str | None:
    html = imp_get_html(pagina_url)
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    imagenes: list[str] = []
    for etiqueta in soup.find_all("img", src=True):
        url = urljoin(pagina_url, str(etiqueta["src"]).strip())
        if url.lower().endswith((".jpg", ".jpeg", ".png", ".webp")) and "/posters/" in url.lower():
            imagenes.append(url)

    for marca in ("_xxlg", "_xlg", "_lg"):
        for imagen_url in imagenes:
            if marca in imagen_url.lower():
                return imagen_url
    return imagenes[0] if imagenes else None


def imp_obtener_peso_imagen(imagen_url: str) -> int:
    try:
        respuesta = requests.head(
            imagen_url,
            headers=IMP_HEADERS,
            timeout=15,
            allow_redirects=True,
        )
        tamano = respuesta.headers.get("Content-Length", "")
        return int(tamano) if tamano.isdigit() else 0
    except (requests.RequestException, ValueError):
        return 0


def imp_encontrar_imagen_mas_grande(poster_url: str) -> str | None:
    candidatas: list[dict[str, Any]] = []
    for pagina in imp_buscar_paginas_de_tamano(poster_url):
        imagen_url = imp_buscar_imagen_en_pagina(str(pagina["url"]))
        if imagen_url:
            candidatas.append(
                {
                    "imagen": imagen_url,
                    "area": int(pagina["area"]),
                    "peso": imp_obtener_peso_imagen(imagen_url),
                }
            )
    if not candidatas:
        return None

    def puntaje(candidata: dict[str, Any]) -> int:
        imagen = str(candidata["imagen"]).lower()
        prioridad = 0
        if "_xxlg" in imagen:
            prioridad = 300_000_000
        elif "_xlg" in imagen:
            prioridad = 200_000_000
        elif "_lg" in imagen:
            prioridad = 100_000_000
        return int(candidata["area"]) or prioridad + int(candidata["peso"])

    return str(max(candidatas, key=puntaje)["imagen"])


def imp_descargar_imagen(imagen_url: str, carpeta: Path, numero: int) -> Path | None:
    nombre_url = limpiar_nombre(Path(urlparse(imagen_url).path).stem, maximo=80)
    temporal = carpeta / f".{numero:03d}_{nombre_url}.part"
    try:
        with requests.get(
            imagen_url,
            headers=IMP_HEADERS,
            timeout=(10, 45),
            stream=True,
            allow_redirects=True,
        ) as respuesta:
            respuesta.raise_for_status()
            total = 0
            with temporal.open("wb") as archivo:
                for bloque in respuesta.iter_content(64 * 1024):
                    if not bloque:
                        continue
                    total += len(bloque)
                    if total > MAXIMO_BYTES:
                        raise ValueError("la imagen supera 50 MB")
                    archivo.write(bloque)

        with Image.open(temporal) as imagen:
            formato = (imagen.format or "").upper()
            imagen.verify()
        if formato not in EXTENSIONES:
            raise ValueError(f"formato no admitido: {formato or 'desconocido'}")

        destino = carpeta / f"{numero:03d}_{nombre_url}{EXTENSIONES[formato]}"
        if destino.exists():
            temporal.unlink(missing_ok=True)
            return destino
        temporal.replace(destino)
        return destino
    except (requests.RequestException, OSError, ValueError, UnidentifiedImageError) as exc:
        temporal.unlink(missing_ok=True)
        imp_safe_print(f"⚠️ No se pudo descargar {imagen_url}: {exc}")
        return None


def imp_procesar_variante(indice: int, poster_url: str, carpeta: Path) -> dict[str, Any]:
    imagen_url = imp_encontrar_imagen_mas_grande(poster_url)
    if not imagen_url:
        return {"indice": indice, "ruta": None, "estado": "sin imagen"}
    ruta = imp_descargar_imagen(imagen_url, carpeta, indice)
    return {"indice": indice, "ruta": ruta, "estado": "ok" if ruta else "error"}


def imp_convertir_a_jpg(ruta_original: Path, carpeta_temporal: Path) -> Path | None:
    if ruta_original.suffix.lower() in (".jpg", ".jpeg"):
        return ruta_original
    ruta_jpg = carpeta_temporal / f"{ruta_original.stem}.jpg"
    try:
        with Image.open(ruta_original) as original:
            if original.mode in ("RGBA", "LA", "P"):
                imagen = original.convert("RGBA")
                fondo = Image.new("RGB", imagen.size, "white")
                fondo.paste(imagen, mask=imagen.getchannel("A"))
                preparada = fondo
            else:
                preparada = original.convert("RGB")
            preparada.save(ruta_jpg, "JPEG", quality=100, subsampling=0, optimize=False)
        return ruta_jpg
    except (OSError, UnidentifiedImageError) as exc:
        imp_safe_print(f"⚠️ No se pudo preparar {ruta_original.name} para PDF: {exc}")
        return None


def imp_crear_pdf(rutas: list[Path], ruta_pdf: Path) -> bool:
    if not rutas:
        return False
    try:
        with tempfile.TemporaryDirectory(prefix="poster_pdf_", dir=ruta_pdf.parent) as temporal:
            carpeta_temporal = Path(temporal)
            preparadas: list[Path] = []
            for ruta in tqdm(rutas, desc="📄 Preparando PDF", unit="img"):
                preparada = imp_convertir_a_jpg(ruta, carpeta_temporal)
                if preparada:
                    preparadas.append(preparada)
            if not preparadas:
                return False
            with ruta_pdf.open("wb") as salida:
                salida.write(img2pdf.convert(*[str(ruta) for ruta in preparadas]))
        return True
    except (OSError, ValueError, TypeError) as exc:
        imp_safe_print(f"⚠️ Error creando el PDF: {exc}")
        return False


def normalizar_link_impawards(url: str) -> str:
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    if "impawards.com" not in urlparse(url).netloc.lower():
        raise ValueError("El enlace debe pertenecer a impawards.com.")
    if not urlparse(url).path.lower().endswith(".html"):
        raise ValueError("El enlace de IMPAwards debe terminar en .html.")
    return url


def pedir_link_impawards() -> str:
    while True:
        respuesta = input("🔗 Pega el enlace de IMPAwards: ").strip()
        try:
            return normalizar_link_impawards(respuesta)
        except ValueError as exc:
            print(f"   ❌ {exc}")


def ejecutar_impawards(url_inicial: str) -> Path | None:
    url_inicial = normalizar_link_impawards(url_inicial)
    year, base = imp_detectar_year_y_base(url_inicial)
    html = imp_get_html(url_inicial)
    if not html:
        print("❌ No se pudo abrir el enlace inicial.")
        return None

    nombre_pdf = imp_extraer_nombre(html, base)
    carpeta_base = Path(__file__).resolve().parent / "IMPAWARDS_DESCARGADOS" / nombre_pdf
    carpeta_imagenes = carpeta_base / "imagenes"
    carpeta_imagenes.mkdir(parents=True, exist_ok=True)
    ruta_pdf = carpeta_base / f"{nombre_pdf}.pdf"
    variantes = imp_buscar_variantes(url_inicial, year, base, html)

    print("\n" + "=" * 72)
    print(" IMPAwards - TODAS LAS VARIANTES + PDF SIN PÉRDIDA")
    print("=" * 72)
    print(f"   Película:   {nombre_pdf}")
    print(f"   Año:        {year}")
    print(f"   Variantes:  {len(variantes)}")
    print(f"   Carpeta:    {carpeta_base}")
    print("=" * 72)

    resultados: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=IMP_MAX_WORKERS) as ejecutor:
        futuros = {
            ejecutor.submit(imp_procesar_variante, indice, url, carpeta_imagenes): indice
            for indice, url in enumerate(variantes, start=1)
        }
        with tqdm(total=len(futuros), desc="⬇️ Descargando variantes", unit="poster") as barra:
            for futuro in as_completed(futuros):
                try:
                    resultado = futuro.result()
                    resultados.append(resultado)
                    barra.set_postfix_str(f"{resultado['estado']}: {resultado['indice']}")
                except Exception as exc:
                    imp_safe_print(f"⚠️ Error en la variante {futuros[futuro]}: {exc}")
                barra.update(1)

    rutas = [
        resultado["ruta"]
        for resultado in sorted(resultados, key=lambda item: int(item["indice"]))
        if isinstance(resultado.get("ruta"), Path) and resultado["ruta"].exists()
    ]
    print(f"\n🖼️ Imágenes disponibles: {len(rutas)}/{len(variantes)}")
    if imp_crear_pdf(rutas, ruta_pdf):
        print(f"✅ PDF creado sin recomprimir los JPG: {ruta_pdf}")
    else:
        print("⚠️ No fue posible crear el PDF.")
    print(f"✅ Proceso terminado: {carpeta_base}")
    return carpeta_base


def mostrar_estado_dependencias() -> bool:
    faltantes = dependencias_faltantes()
    print("\n🔎 COMPROBACIÓN DEL SISTEMA")
    print(f"   Sistema: {sys.platform}")
    print(f"   Python:  {sys.version.split()[0]}")
    print(f"   Ruta:    {sys.executable}")
    print("   VENV automático: DESACTIVADO")
    for modulo, paquete in PAQUETES:
        estado = "OK" if importlib.util.find_spec(modulo) is not None else "FALTA"
        print(f"   [{estado:^5}] {paquete}")
    if faltantes:
        print("\n❌ Faltan componentes: " + ", ".join(faltantes))
        return False
    print("\n✅ Todos los componentes requeridos están instalados.")
    return True


def mostrar_ayuda_toolbox() -> None:
    print(
        f"""
POSTER TOOLBOX PRO {VERSION}

Uso interactivo:
  python POSTER_TOOLBOX_PRO.py

Buscar pósteres por tema:
  python POSTER_TOOLBOX_PRO.py buscar "Harry Potter" -o vertical -n 20

Descargar variantes de IMPAwards y crear PDF:
  python POSTER_TOOLBOX_PRO.py impawards ENLACE

Comprobar los componentes instalados:
  python POSTER_TOOLBOX_PRO.py --check
"""
    )


def menu_principal() -> int:
    while True:
        print("\n" + "=" * 72)
        print(f" POSTER TOOLBOX PRO {VERSION}")
        print("=" * 72)
        print("  1. Buscar pósteres por tema (DDGS)")
        print("  2. Descargar variantes de IMPAwards + PDF")
        print("  3. Comprobar requerimientos instalados")
        print("  0. Salir")
        print("=" * 72)
        opcion = input("Elige una opción: ").strip()

        try:
            if opcion == "1":
                ejecutar_busqueda_general([])
            elif opcion == "2":
                carpeta = ejecutar_impawards(pedir_link_impawards())
                if carpeta:
                    respuesta = input("\n📂 ¿Abrir la carpeta ahora? [S/n]: ").strip().lower()
                    if respuesta in ("", "s", "si", "sí"):
                        abrir_carpeta(carpeta)
            elif opcion == "3":
                mostrar_estado_dependencias()
            elif opcion == "0":
                print("\n👋 Hasta luego.")
                return 0
            else:
                print("   ❌ Elige 1, 2, 3 o 0.")
                continue
        except KeyboardInterrupt:
            print("\n⚠️ Operación cancelada; regresando al menú.")
        except Exception as exc:
            print(f"\n❌ Ocurrió un error: {exc}")
        input("\nPresiona ENTER para volver al menú...")


def main_toolbox(argv: list[str] | None = None) -> int:
    argumentos = list(sys.argv[1:] if argv is None else argv)
    if not argumentos:
        return menu_principal()

    comando = argumentos[0].lower()
    if comando in ("buscar", "busqueda", "búsqueda"):
        return ejecutar_busqueda_general(argumentos[1:])
    if comando == "impawards":
        url = argumentos[1] if len(argumentos) > 1 else pedir_link_impawards()
        return 0 if ejecutar_impawards(url) else 1
    if comando == "--check":
        return 0 if mostrar_estado_dependencias() else 1
    if comando in ("-h", "--help", "ayuda"):
        mostrar_ayuda_toolbox()
        return 0

    # Compatibilidad con el uso anterior: el primer argumento puede ser el tema.
    return ejecutar_busqueda_general(argumentos)


if __name__ == "__main__":
    try:
        raise SystemExit(main_toolbox())
    except KeyboardInterrupt:
        print("\n\n⚠️ Proceso cancelado por el usuario.")
        raise SystemExit(130)
