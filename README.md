# Alpha-ESS charging optimizer 🔋

This project aims to optimize charging and discharging times for your Alpha ESS based on day-ahead prices on the Czech energy market, 
in order to minimize the cost of charging and to utilize the battery's potential without human intervention. 

Our battery takes around 3 hours to charge from 10% to full, therefore a 3h slot between 00:00 and 06:00 with the lowest price is found. 
Discharging slot is also is set to begin between 00:00 and 12:00, since we want to make sure all the stored power is used during the day.

To access Alpha ESS API, you need to provide a username and password in `config.json`. Happy charging! ☀️

### Automation
To automate this, you ought to create a cron job to run `ESS.py` every evening to compute and set the charge and discharge times for the following day.
I setup my crontab as follows:
```
00 18 * * * /usr/bin/python3 /home/pi/AlphaESS/ESS.py
```
Bear in mind that setting up a cron job also means that the log file will be created in your home directory.
