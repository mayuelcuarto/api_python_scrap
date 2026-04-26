from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import time
import re
import uvicorn
from threading import Semaphore

app = FastAPI(title="Soccer Scraper API")

# Limitar el número de navegadores abiertos simultáneamente (ajusta según tu RAM)
MAX_CONCURRENT_SCRAPERS = 3
scraper_semaphore = Semaphore(MAX_CONCURRENT_SCRAPERS)

# Instalar el driver una sola vez al inicio para mejorar rendimiento
CHROME_DRIVER_PATH = ChromeDriverManager().install()
chrome_service = Service(CHROME_DRIVER_PATH)

# Configuración de CORS para permitir peticiones desde Angular (habitualmente puerto 4200)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # En producción, usa ["http://localhost:4200"]
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_match_stats(url: str):
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    # Evitar detección básica de bot y mejorar compatibilidad
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

    # Usamos el servicio pre-configurado
    driver = webdriver.Chrome(service=chrome_service, options=chrome_options)
    try:
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        driver.get(url)
        wait = WebDriverWait(driver, 20) # Aumentamos un poco el margen
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        
        # Intentar hacer clic en la pestaña "Estadísticas"
        try:
            # Buscamos de forma más flexible y esperamos a que sea clickable
            xpath_stats = "//div[contains(., 'Estadísticas')] | //span[contains(., 'Estadísticas')] | //a[contains(., 'Estadísticas')]"
            boton_stats = wait.until(EC.element_to_be_clickable((By.XPATH, xpath_stats)))
            driver.execute_script("arguments[0].click();", boton_stats)
            # En lugar de sleep fijo, esperamos a que aparezca un elemento clave de las estadísticas
            wait.until(EC.presence_of_element_located((By.XPATH, "//div[contains(., 'Grandes chances')] | //div[contains(., 'Faltas')]")))
        except Exception:
            pass # Si falla el clic, intentamos extraer lo que sea visible
            print("No se pudo hacer clic en la pestaña de estadísticas o no cargó a tiempo.")

        soup = BeautifulSoup(driver.page_source, 'html.parser')
        results = {}

        # --- Extracción del Marcador (Score) ---
        try:
            # Buscamos el nodo de texto que contiene únicamente el guion "-" separador
            dash_node = soup.find(string=re.compile(r"^\s*-\s*$"))
            if dash_node:
                # Intentamos obtener los números del contenedor padre
                container = dash_node.parent
                # Usamos un separador para identificar números aislados y evitar capturar minutos como "90'"
                texto_area = container.get_text(separator='|', strip=True)
                
                # Si el padre directo no tiene los marcadores, subimos un nivel (común en estructuras de encabezado)
                numeros = [n.strip() for n in texto_area.split('|') if n.strip().isdigit()]
                if len(numeros) < 2:
                    texto_area = container.parent.get_text(separator='|', strip=True)
                    numeros = [n.strip() for n in texto_area.split('|') if n.strip().isdigit()]

                if len(numeros) >= 2:
                    results["score_equipo1"] = numeros[0]
                    results["score_equipo2"] = numeros[1]
            
            if "score_equipo1" not in results:
                results["score_equipo1"] = "-1"
                results["score_equipo2"] = "-1"
        except Exception:
            results["score_equipo1"] = "-1"
            results["score_equipo2"] = "-1"

        stats_interes = [
            "Goles esperados",
            "Total Remates",
            "Remates al arco",
            "Grandes chances",
            "Saques de esquina",
            "Salvadas de Portero",
            "Faltas",
            "Faltas recibidas",
            "Tarjetas Amarillas",
            "Tarjetas Rojas",
            "Posesión",
            "Pases completados",
            "Pases en el propio campo",
            "Pases en el campo contrario",
        ]

        # Buscamos todos los SPANs dentro del contenedor de estadísticas
        all_spans = soup.find_all("span")

        for stat_name in stats_interes:
            # Diccionario de reemplazo rápido
            trans = str.maketrans("áéíóúüñÁÉÍÓÚÜÑ", "aeiouunAEIOUUN")
            key_base = stat_name.translate(trans).lower().replace(" ", "_")
            found = False

            # Creamos un regex flexible para los espacios y saltos de línea internos
            # Esto soluciona lo de "Remates al       arco"
            # Usamos \b para límites de palabra y un lookahead negativo (?!\s*[a-zA-ZáéíóúüñÁÉÍÓÚÜÑ])
            # para evitar que "Faltas" coincida con "Faltas recibidas"
            escaped_name = re.escape(stat_name).replace(r'\ ', r'\s+')
            stat_regex = fr"\b{escaped_name}\b(?!\s*[a-zA-ZáéíóúüñÁÉÍÓÚÜÑ])"
            pattern = re.compile(stat_regex, re.IGNORECASE)

            for span in all_spans:
                # get_text(separator=" ") une el texto antes y después del <br /> con un espacio
                text_content = span.get_text(separator=" ", strip=True)
                
                if pattern.search(text_content):
                    # Buscamos los números (incluyendo decimales como 2.5)
                    # El regex busca grupos de números separados por guion, espacios o barra
                    # Ejemplo: "Remates al arco 7 - 5" -> ["7", "5"]
                    numbers = re.findall(r"(\d+\.?\d*)", text_content)
                    
                    if len(numbers) >= 2:
                        # En Scores, el primer número es Local, el segundo es Visitante
                        results[f"{key_base}_equipo1"] = numbers[0]
                        results[f"{key_base}_equipo2"] = numbers[1]
                        found = True
                        break
            
            if not found:
                results[f"{key_base}_equipo1"] = "-1"
                results[f"{key_base}_equipo2"] = "-1"

        return results

    finally:
        driver.quit()

@app.get("/api/stats")
def stats_endpoint(url: str = Query(..., description="URL de Scores")):
    """
    Recibe la URL de un partido y devuelve un JSON con las estadísticas.
    """
    # El semáforo asegura que solo N hilos entren aquí a la vez, el resto espera
    with scraper_semaphore:
        try:
            data = get_match_stats(url)
            return data
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
def health():
    return {"status": "alive"}

if __name__ == "__main__":
    # Ejecución del servidor en el puerto 5000
    uvicorn.run(app, host="127.0.0.1", port=5000)