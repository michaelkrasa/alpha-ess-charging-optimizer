# Alpha-ESS charging optimizer

This project aims to optimize charging and discharging times for your Alpha ESS based on day-ahead prices on the Czech energy market, 
in order to minimize the cost of charging and to utilize the battery's potential without human intervention. 
This python file is set to run once a day at 6 PM to scrape the www.ote-cr.cz website, compute and set optimal charge and discharge times for the following day.

Our battery takes around 3 hours to charge from 10% to full, therefore a 3h slot between 00:00 and 06:00 with the lowest price is found. 
For discharging, discharging is set to begin between 00:00 and 12:00, since we want to make sure all the stored power is used during the day.

To access Alpha ESS API, you only need to provide a username and password in config.json. Happy charging!
