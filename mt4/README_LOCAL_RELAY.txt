FXPilot MT4 Bridge v2.18 LOCAL

1. Copy FXPilot_MT4_Bridge.mq4 to MT4: File -> Open Data Folder -> MQL4 -> Experts.
2. Compile it in MetaEditor and attach it to ONE chart.
3. Enter ApiToken in the EA settings.
4. Leave UseLocalRelay=true.
5. Double-click Start_FXPilot_Relay.cmd and KEEP its window open.
6. In MT4 click SEND NOW.

Expected relay message:
OK 200 <date/time>

The relay watches:
%APPDATA%\MetaQuotes\Terminal\Common\Files\fxpilot_job.json

Security: the API token is stored only in the local temporary job file. Rotate the
token that appeared in screenshots before production use.
