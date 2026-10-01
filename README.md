# GSX-R600 Hunter

Searches motorcycle sites for a 1993 Suzuki GSX-R600 (red/white with
orange accents) and sends alerts to my phone through ntfy.

- Checks every hour and alerts on new matches
- Sends a daily report at about 8am US Central, even when nothing is found
- Runs automatically on GitHub Actions

Files:
- bike_hunter_part_two.py - the search script
- requirements_part_two.txt - Python packages it needs
- .github/workflows/hunt_part_two.yml - the schedule
