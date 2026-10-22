"""Plot the cash-versus-stock distinction from the original-resolution replay."""

import json
from pathlib import Path
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
frame = pd.DataFrame(json.loads((ROOT/"analysis/output/emhass_benchmark_week_trace_trace.json").read_text()))
frame["time"] = pd.to_datetime(frame.timestamp,utc=True).dt.tz_convert("Europe/Prague")
fig,axes = plt.subplots(3,1,figsize=(12,8),sharex=True,gridspec_kw={"height_ratios":[1,1.4,1.1]})
colors = {"current":"#3c6645","emhass":"#ba702e","native":"#8a9489","no_battery":"#b5b3aa"}
actual = frame[frame.policy=="current"].sort_values("time")
axes[0].step(actual.time,actual.import_eur_kwh*1000,where="post",color="#616857",linewidth=1.2)
axes[0].set_ylabel("Import EUR/MWh")
for key,label in [("current","Window search"),("emhass","EMHASS MPC")]:
    actual = frame[frame.policy==key].sort_values("time")
    axes[1].step(actual.time+pd.Timedelta(minutes=15),actual.soc_after,where="post",color=colors[key],label=label,linewidth=1.4)
for key,label in [("current","Window search"),("emhass","EMHASS MPC"),("native","Native battery"),("no_battery","No battery")]:
    actual = frame[frame.policy==key].sort_values("time")
    axes[2].plot(actual.time+pd.Timedelta(minutes=15),actual.cash_eur.cumsum(),color=colors[key],label=label,linewidth=1.4)
axes[1].set_ylabel("Battery SOC %")
axes[1].set_ylim(0,105)
axes[1].axhline(10,color="#9b9e94",linestyle=":",linewidth=.8)
axes[1].legend(loc="upper left",ncol=2,frameon=False)
axes[2].set_ylabel("Cumulative bill EUR")
axes[2].legend(loc="upper left",ncol=2,frameon=False)
axes[-1].xaxis.set_major_locator(mdates.DayLocator(tz=ZoneInfo("Europe/Prague")))
axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%a %d Oct",tz=ZoneInfo("Europe/Prague")))
for ax in axes:
    ax.grid(axis="both",alpha=.15)
    ax.spines[["top","right"]].set_visible(False)
fig.suptitle("A larger bill can include energy bought for the following day",fontsize=15,x=.08,ha="left")
fig.text(.08,.015,"12–18 October 2026 · Modeled household replay, EV interlock retained · Same initial 50% SOC\nEMHASS ends at 73%, search at 10%. A common native-only continuation values that extra stock separately.",fontsize=10,color="#50594b")
fig.tight_layout(rect=(0,.065,1,.95))
fig.savefig(ROOT/"analysis/output/emhass_week_inventory.png",dpi=150)
plt.close(fig)
