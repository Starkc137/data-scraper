import json
import csv

# Load JSON data
with open('uefa_player_stats.json', 'r', encoding='utf-8') as file:
    data = json.load(file)

# Prepare CSV output
csv_filename = 'uefa_player_stats.csv'
with open(csv_filename, mode='w', newline='', encoding='utf-8-sig') as csvfile:

    fieldnames = [
        'name', 'nation', 'position', 'age',
        'matches_played', 'minutes_played', 'goals', 'assists',
        'penalties_scored', 'yellow_cards', 'red_cards',
        'team_name'
    ]
    writer = csv.DictWriter(csvfile, fieldnames=fieldnames, quoting=csv.QUOTE_MINIMAL)

    writer.writeheader()

    for group in data.get("all_results", []):
        for entry in group:
            try:
                player_info = entry.get("player", {})
                statistics = entry.get("statistics", [])

                # Extract stats into a dictionary
                stats = {stat["name"]: stat["value"] for stat in statistics}

                writer.writerow({
                    'name': player_info.get("internationalName"),
                    'nation': player_info.get("translations", {}).get("countryName", {}).get("EN"),
                    'position': player_info.get("fieldPosition"),
                    'age': int(player_info.get("age")) if player_info.get("age") else None,
                    'matches_played': float(stats.get("matches_appearance", 0)),
                    'minutes_played': float(stats.get("minutes_played_official", 0)),
                    'goals': float(stats.get("goals", 0)),
                    'assists': float(stats.get("assists", 0)),
                    'penalties_scored': float(stats.get("penalty_scored", 0)),
                    'yellow_cards': float(stats.get("yellow_cards", 0)),
                    'red_cards': float(stats.get("red_cards", 0)),
                    'team_name': entry.get("team", {}).get("translations", {}).get("displayName", {}).get("EN")
                })

            except Exception as e:
                print(f"Error processing player entry: {e}")
