import json
import time
from selenium import webdriver 
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager 

# Set up Chrome options
options = Options()
# Enable performance logging
options.set_capability("goog:loggingPrefs", {"performance": "ALL", "browser": "ALL"})
service = ChromeService(executable_path=r"C:\Users\nhlakanipho\Downloads\chromedriver-win64\chromedriver.exe")
driver = webdriver.Chrome(service=service, options=options)
driver.set_page_load_timeout(30)  # Increased timeout for stability

# Function to extract JSON from browser logs
def extract_json_from_logs(driver):
    logs = driver.get_log("performance")
    for log in logs:
        message = json.loads(log["message"])["message"]
        if message["method"] == "Network.responseReceived" and "json" in message.get("params", {}).get("response", {}).get("mimeType", ""):
            request_id = message["params"]["requestId"]
            try:
                response = driver.execute_cdp_cmd("Network.getResponseBody", {"requestId": request_id})
                return json.loads(response["body"])
            except:
                pass
    return None

# Function to write data to file
def write_to_json_file(data, filename):
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

# Main collection function
def collect_uefa_player_data(base_url, max_offset=1095, step=15):
    all_data = []
    
    for offset in range(0, max_offset+1, step):
        url = f"{base_url}&offset={offset}"
        print(f"Fetching data with offset {offset}...")
        
        try:
            driver.get(url)
            # Wait for data to load
            time.sleep(2)
            
            # Extract JSON from logs
            json_data = extract_json_from_logs(driver)
            
            if json_data:
                # If it's the first batch, save the structure
                if offset == 0:
                    write_to_json_file(json_data, "sofascore_first_batch.json")
                
                # Add to our collection (adjust this based on the actual JSON structure)
                if "results" in json_data:
                    all_data.extend(json_data["results"])
                else:
                    all_data.append(json_data)
                
                print(f"Successfully collected data for offset {offset}")
            else:
                print(f"No valid JSON data found for offset {offset}")
        
        except Exception as e:
            print(f"Error fetching offset {offset}: {e}")
        
        # Be nice to the server
        time.sleep(1)
    
    # Save all collected data
    write_to_json_file({"all_results": all_data}, "uefa_player_stats.json")
    print(f"Data collection complete. Saved to 'uefa_player_stats.json'")

# Base URL without the offset parameter
# base_url = "https://www.sofascore.com/api/v1/unique-tournament/7/season/61644/statistics?limit=0&order=-rating&accumulation=total&group=summary"
base_url = "https://compstats.uefa.com/v1/player-ranking?competitionId=1&limit=15&optionalFields=PLAYER%2CTEAM&order=DESC&phase=TOURNAMENT&seasonYear=2025&stats=minutes_played_official%2Cmatches_appearance%2Cgoals%2Cassists%2Cpenalty_scored%2Cyellow_cards%2Cred_cards"


# Run the collection
try:
    collect_uefa_player_data(base_url)
finally:
    driver.quit()