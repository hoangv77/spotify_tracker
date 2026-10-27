"""A local data preview; this image is not a Power BI Desktop rendering."""

from pathlib import Path
import textwrap

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.colors import LinearSegmentedColormap
import numpy as np
import pandas as pd


def generate_preview(folder: Path, summary: dict, aggregates: dict) -> None:
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "text.color": "#EDF5EF", "axes.labelcolor": "#A9BDB0", "xtick.color": "#A9BDB0", "ytick.color": "#A9BDB0", "axes.edgecolor": "#304339", "axes.facecolor": "#17241F", "figure.facecolor": "#0D1712", "axes.titlecolor": "#EDF5EF"})
    fig = plt.figure(figsize=(16, 10), layout=None)
    fig.text(.045, .945, "YOUR LISTENING, OVER TIME", fontsize=24, weight="bold")
    fig.text(.045, .914, f"{summary['start_date']} - {summary['end_date']}  |  {summary['timezone']}  |  Data preview · open the PBIP for interactive reporting", color="#A9BDB0", fontsize=10)
    values = [("LISTENING HOURS", f"{summary['listening_hours']:,.0f}"), ("QUALIFIED PLAYS", f"{summary['qualified_plays']:,}"), ("UNIQUE ARTISTS", f"{summary['unique_artists']:,}"), ("UNIQUE SONGS", f"{summary['unique_songs']:,}"), ("DISCOVERY RATE", f"{summary['discovery_rate']:.1%}")]
    for i, (label, value) in enumerate(values):
        x = .045 + i * .19
        fig.text(x, .856, label, color="#A9BDB0", fontsize=9)
        fig.text(x, .81, value, color="#1ED760", fontsize=25, weight="bold")
    grid = fig.add_gridspec(2, 2, left=.065, right=.965, top=.745, bottom=.11, hspace=.43, wspace=.28, width_ratios=[1.25, 1])
    ax = fig.add_subplot(grid[0, 0])
    monthly = aggregates["monthly_metrics"]
    dates = pd.to_datetime(monthly["month"] + "-01")
    ax.plot(dates, monthly["listening_hours"], color="#1ED760", linewidth=2)
    ax.fill_between(dates, monthly["listening_hours"], color="#1ED760", alpha=.12)
    ax.set_title("Listening hours by month", loc="left", pad=15, weight="bold")
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.set_ylabel("Hours")
    ax.grid(axis="y", alpha=.12)
    ax = fig.add_subplot(grid[0, 1])
    top = aggregates["top_artists"].head(8).iloc[::-1]
    labels = [textwrap.shorten(s, width=24, placeholder="...") for s in top["artist_name"]]
    ax.barh(labels, top["listening_hours"], color="#1ED760", height=.6)
    ax.set_title("Top artists · listening hours", loc="left", pad=15, weight="bold")
    ax.set_xlabel("Hours")
    ax.tick_params(axis="y", labelsize=9)
    ax.grid(axis="x", alpha=.12)
    ax = fig.add_subplot(grid[1, 0])
    ax.plot(dates, monthly["discovery_rate"] * 100, color="#6CD4C5", label="Song discovery", linewidth=2)
    ax.plot(dates, monthly["artist_loyalty"] * 100, color="#BBA0F4", label="Artist loyalty", linewidth=2)
    ax.set_title("Discovery & returning artists", loc="left", pad=15, weight="bold")
    ax.set_ylim(0, 105)
    ax.set_ylabel("Qualified plays (%)")
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.legend(loc="upper left", bbox_to_anchor=(0, -.14), ncol=2, frameon=False, fontsize=9)
    ax.grid(axis="y", alpha=.12)
    ax = fig.add_subplot(grid[1, 1])
    hourly = aggregates["hourly_patterns"].pivot(index="weekday_number", columns="hour", values="listening_hours").reindex(index=range(7), columns=range(24)).fillna(0)
    cmap = LinearSegmentedColormap.from_list("listening", ["#17241F", "#227B49", "#1ED760"])
    image = ax.imshow(hourly.to_numpy(), aspect="auto", cmap=cmap)
    ax.set_yticks(range(7), ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
    ax.set_xticks([0, 6, 12, 18, 23], ["00", "06", "12", "18", "23"])
    ax.set_title("When you listen · total hours", loc="left", pad=15, weight="bold")
    ax.set_xlabel("Local playback-end hour")
    fig.colorbar(image, ax=ax, fraction=.035, pad=.03, label="Hours")
    for ax in fig.axes:
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
    fig.text(.045, .02, f"Qualified play: {summary['qualified_play_seconds']:g}+ sec  ·  Hours include short plays  ·  Genre coverage: {summary['genre_coverage']:.1%}  ·  Boundary periods are partial", color="#A9BDB0", fontsize=9)
    fig.savefig(folder / "dashboard_preview.png", dpi=150)
    fig.savefig(folder / "dashboard_preview.svg")
    plt.close(fig)
