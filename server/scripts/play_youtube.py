import sys
import time
import urllib.parse
import webbrowser
import pyautogui

def play_song(query):
    # Genera la URL de búsqueda con la canción
    encoded_query = urllib.parse.quote(query)
    url = f"https://www.youtube.com/results?search_query={encoded_query}"
    
    # Abre el navegador predeterminado
    webbrowser.open(url)
    
    # Espera 3 segundos a que cargue la página
    time.sleep(3)
    
    # Presiona Tab para ir al primer resultado y Enter para abrirlo
    pyautogui.press('tab')
    pyautogui.press('enter')

if __name__ == "__main__":
    if len(sys.argv) > 1:
        song_name = " ".join(sys.argv[1:])
        play_song(song_name)