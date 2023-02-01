# Alpha-ESS charging optimizer 🔋

This project aims to maximize the cost saving potential of your Alpha ESS by optimizing times for night charging and discharging during the morning peak hours. The calculation is based on the [day-ahead prices](https://www.ote-cr.cz/en/short-term-markets/electricity/day-ahead-market) of the Czech energy market.

Best charging slot, of configutable length, is found between 00:00 and 07:00 while the discharging slot is found between 00:00 and 12:00 to make sure all the stored power is used throughout the day.

To begin using this, clone the repository, fill out required fields in `config.yaml` and run the program to find and set prices for the following day. Happy charging! ☀️

## Automation
To fully automate this, you ought to create a cron job to run `ESS.py` every day after spot prices on the next day are published.
My crontab setup running on a RaspberryPi:
```
00 18 * * * /usr/bin/python3 /home/pi/AlphaESS/ESS.py
```

