"""Generate editable PBIP/PBIR source and a CSV-backed tabular semantic model."""

from __future__ import annotations

import json
from pathlib import Path


BASE = "https://developer.microsoft.com/json-schemas/fabric/"


def save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def literal(value) -> dict:
    if isinstance(value, bool):
        text = str(value).lower()
    elif isinstance(value, (int, float)):
        text = str(value) + "D"
    else:
        text = "'" + str(value).replace("'", "''") + "'"
    return {"expr": {"Literal": {"Value": text}}}


def color(value: str) -> dict:
    return {"solid": {"color": literal(value)}}


MEASURES = [
    ("Listening Hours", "DIVIDE(SUM(Listening[ms_played]), 3600000)", "#,0.0", "All valid music duration, including plays shorter than the qualification threshold."),
    ("Qualified Plays", "SUM(Listening[qualified_play])", "#,0", "Music events at or above the pipeline's configured duration threshold (default 30 seconds)."),
    ("Unique Artists", "CALCULATE(DISTINCTCOUNT(Listening[artist_key]), Listening[qualified_play] = 1)", "#,0", "Artists with a qualified play in the current filter context."),
    ("Unique Songs", "CALCULATE(DISTINCTCOUNT(Listening[track_key]), Listening[qualified_play] = 1)", "#,0", "Songs with a qualified play in the current filter context."),
    ("New Songs", "SUM(Listening[is_new_song])", "#,0", "Songs whose first observed qualifying event falls within the selected context."),
    ("New Artists", "SUM(Listening[is_new_artist])", "#,0", "Artists whose first observed qualifying event falls within the selected context."),
    ("Discovery Rate", "DIVIDE([New Songs], [Qualified Plays])", "0.0%", "First-observed qualifying song events divided by all qualifying plays. Discovery is relative to available export history."),
    ("Artist Loyalty", "DIVIDE(SUM(Listening[is_returning_artist]), [Qualified Plays])", "0.0%", "Fraction of qualifying plays for artists first heard on a strictly earlier local date."),
    ("Known Skip Rate", "DIVIDE(CALCULATE(COUNTROWS(Listening), Listening[skipped] = 1), CALCULATE(COUNTROWS(Listening), NOT ISBLANK(Listening[skipped])))", "0.0%", "Explicit true skips divided by music events with a known skip flag; null flags are excluded."),
    ("Genre Coverage", 'DIVIDE(CALCULATE([Listening Hours], KEEPFILTERS(Artists[primary_genre] <> "Unknown")), [Listening Hours])', "0.0%", "Share of listening hours mapped to a verified primary artist genre, in current context."),
    ("Average Minutes per Day", "DIVIDE([Listening Hours] * 60, COUNTROWS(Dates))", "#,0.0", "Minutes divided by selected calendar dates, including dates with no music events."),
    ("Top 5 Artist Share", "VAR TopArtists = TOPN(5, ALLSELECTED(Artists), [Listening Hours], DESC, Artists[artist_key], ASC) RETURN DIVIDE(SUMX(TopArtists, [Listening Hours]), CALCULATE([Listening Hours], ALLSELECTED(Artists)))", "0.0%", "Listening time to the selected population's five most-listened artists, divided by total selected artist listening time."),
    ("Top Artist Hours", "IF(RANKX(ALLSELECTED(Artists[artist_name]), [Listening Hours], , DESC, DENSE) <= 10, [Listening Hours], BLANK())", "#,0.0", "Hours for the ten leading artist ranks under the current filters; ties can show more than ten."),
    ("Top Song Plays", "IF(RANKX(ALLSELECTED(Tracks[track_label]), [Qualified Plays], , DESC, DENSE) <= 10, [Qualified Plays], BLANK())", "#,0", "Qualified plays for the ten leading song ranks under the current filters; ties can show more than ten."),
]


def make_model(data_folder: Path) -> dict:
    # Explicit CSV types make refresh independent of locale and type inference.
    schemas = {
        "Listening": ("fact_listening", {
            "event_id": "string", "ended_at_utc": "string", "date": "dateTime", "hour": "int64", "weekday_number": "int64", "weekday": "string", "year": "int64", "month": "string", "artist_key": "string", "track_key": "string", "ms_played": "int64", "minutes_played": "double", "qualified_play": "int64", "is_new_artist": "int64", "is_new_song": "int64", "is_returning_artist": "int64", "skipped": "int64", "reason_end": "string", "platform": "string", "stream_type": "string"}),
        "Artists": ("dim_artists", {"artist_key": "string", "artist_name": "string", "first_heard_date": "dateTime", "primary_genre": "string", "source": "string"}),
        "Tracks": ("dim_tracks", {"track_key": "string", "track_name": "string", "artist_key": "string", "album_name": "string", "spotify_track_uri": "string", "first_heard_date": "dateTime", "track_label": "string"}),
        "Dates": ("dim_dates", {"date": "dateTime", "year": "int64", "month": "string", "month_number": "int64", "weekday_number": "int64", "weekday": "string", "is_weekend": "int64"}),
    }
    m_types = {"string": "type text", "dateTime": "type date", "int64": "Int64.Type", "double": "type number"}
    tables = []
    for name, (filename, schema) in schemas.items():
        transform = ", ".join('{"' + column + '", ' + m_types[datatype] + '}' for column, datatype in schema.items())
        query = ["let", f'    Source = Csv.Document(File.Contents(DataFolder & "/{filename}.csv"), [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),', '    Headers = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),', '    BlanksToNull = Table.ReplaceValue(Headers, "", null, Replacer.ReplaceValue, Table.ColumnNames(Headers)),', f'    Typed = Table.TransformColumnTypes(BlanksToNull, {{{transform}}}, "en-US")', "in", "    Typed"]
        columns = []
        for column, datatype in schema.items():
            item = {"name": column, "dataType": datatype, "sourceColumn": column, "summarizeBy": "none"}
            if datatype == "dateTime":
                item["formatString"] = "yyyy-MM-dd"
            if column.endswith("_key") or column == "event_id":
                item["isHidden"] = True
            if name == "Dates" and column == "date":
                item["isKey"] = True
                item["annotations"] = [{"name": "UnderlyingDateTimeDataType", "value": "Date"}]
            if name == "Dates" and column == "weekday":
                item["sortByColumn"] = "weekday_number"
            columns.append(item)
        table = {"name": name, "columns": columns, "partitions": [{"name": name, "mode": "import", "source": {"type": "m", "expression": query}}]}
        if name == "Listening":
            table["measures"] = [{"name": n, "expression": expr, "formatString": fmt, "description": desc, "displayFolder": "Listening metrics"} for n, expr, fmt, desc in MEASURES]
        if name == "Dates":
            table["dataCategory"] = "Time"
        tables.append(table)
    return {
        "name": "SpotifyTracker", "compatibilityLevel": 1600,
        "model": {
            "culture": "en-US", "sourceQueryCulture": "en-US",
            "defaultPowerBIDataSourceVersion": "powerBI_V3", "discourageImplicitMeasures": True,
            "expressions": [{"name": "DataFolder", "kind": "m", "expression": json.dumps(data_folder.resolve().as_posix()) + ' meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]'}],
            "tables": tables,
            "relationships": [{"name": f"Listening_{dimension}", "fromTable": "Listening", "fromColumn": key, "toTable": dimension, "toColumn": key, "crossFilteringBehavior": "oneDirection"} for dimension, key in [("Dates", "date"), ("Artists", "artist_key"), ("Tracks", "track_key")]],
            "annotations": [{"name": "PBI_QueryOrder", "value": '["DataFolder","Dates","Artists","Tracks","Listening"]'}, {"name": "PBI_TimeIntelligenceEnabled", "value": "0"}],
        },
    }


def field(table: str, column: str, measure: bool = False) -> dict:
    return {"Measure" if measure else "Column": {"Expression": {"SourceRef": {"Entity": table}}, "Property": column}}


def projection(table: str, column: str, measure: bool = False) -> dict:
    return {"field": field(table, column, measure), "queryRef": f"{table}.{column}", "nativeQueryRef": column}


def chart(name: str, kind: str, title: str, x: int, y: int, width: int, height: int, roles: dict, sort: tuple | None = None) -> dict:
    visual = {"visualType": kind, "query": {"queryState": {role: {"projections": [projection(*p) for p in values]} for role, values in roles.items()}}, "drillFilterOtherVisuals": True,
        "visualContainerObjects": {
            "title": [{"properties": {"show": literal(True), "text": literal(title), "fontSize": literal(12), "fontColor": color("#F5F7FA"), "fontFamily": literal("Segoe UI Semibold")}}],
            "background": [{"properties": {"show": literal(True), "color": color("#17241F"), "transparency": literal(0)}}],
            "border": [{"properties": {"show": literal(True), "color": color("#304339"), "radius": literal(8)}}],
            "general": [{"properties": {"altText": literal(title)}}],
        },
    }
    if sort:
        visual["query"]["sortDefinition"] = {"sort": [{"field": field(*sort[:3]), "direction": sort[3]}], "isDefaultSort": True}
    if kind == "slicer":
        visual["objects"] = {"data": [{"properties": {"mode": literal("Dropdown")}}]}
    return {"$schema": BASE + "item/report/definition/visualContainer/2.1.0/schema.json", "name": name, "position": {"x": x, "y": y, "z": 0, "width": width, "height": height, "tabOrder": y * 10 + x}, "visual": visual}


def textbox(name: str, title: str, subtitle: str) -> dict:
    result = chart(name, "textbox", title, 32, 22, 1216, 80, {})
    result["visual"]["visualContainerObjects"]["title"] = [{"properties": {"show": literal(False)}}]
    result["visual"]["visualContainerObjects"]["background"] = [{"properties": {"show": literal(False)}}]
    result["visual"]["visualContainerObjects"]["border"] = [{"properties": {"show": literal(False)}}]
    result["visual"]["objects"] = {"general": [{"properties": {"paragraphs": [
        {"textRuns": [{"value": title, "textStyle": {"fontFamily": "Segoe UI Semibold", "fontSize": "26pt", "color": "#F5F7FA"}}]},
        {"textRuns": [{"value": subtitle, "textStyle": {"fontFamily": "Segoe UI", "fontSize": "10pt", "color": "#A9BDB0"}}]},
    ]}}]}
    return result


def generate_project(folder: Path, data_folder: Path) -> None:
    report = folder / "SpotifyTracker.Report"
    model = folder / "SpotifyTracker.SemanticModel"
    save(folder / "SpotifyTracker.pbip", {"$schema": BASE + "pbip/pbipProperties/1.0.0/schema.json", "version": "1.0", "artifacts": [{"report": {"path": "SpotifyTracker.Report"}}], "settings": {"enableAutoRecovery": True}})
    save(model / "definition.pbism", {"$schema": BASE + "item/semanticModel/definitionProperties/1.0.0/schema.json", "version": "1.0", "settings": {"qnaEnabled": False}})
    save(model / "model.bim", make_model(data_folder))
    save(report / "definition.pbir", {"$schema": BASE + "item/report/definitionProperties/2.0.0/schema.json", "version": "4.0", "datasetReference": {"byPath": {"path": "../SpotifyTracker.SemanticModel"}}})
    definition = report / "definition"
    save(definition / "version.json", {"$schema": BASE + "item/report/definition/versionMetadata/1.0.0/schema.json", "version": "2.0.0"})
    theme = {
        "name": "Spotify Tracker", "dataColors": ["#1ED760", "#6CD4C5", "#BBA0F4", "#FFCF70", "#76AAFF", "#F49AAB", "#91B77E", "#C0CCD0"],
        "background": "#0D1712", "foreground": "#F5F7FA", "tableAccent": "#1ED760", "good": "#1ED760", "neutral": "#FFCF70", "bad": "#F49AAB",
        "textClasses": {"label": {"color": "#A9BDB0", "fontFace": "Segoe UI"}, "title": {"color": "#F5F7FA", "fontFace": "Segoe UI Semibold"}, "callout": {"color": "#1ED760", "fontFace": "Segoe UI Semibold"}},
        "visualStyles": {"*": {"*": {"background": [{"color": {"solid": {"color": "#17241F"}}, "transparency": 0}], "categoryAxis": [{"labelColor": {"solid": {"color": "#A9BDB0"}}}], "valueAxis": [{"labelColor": {"solid": {"color": "#A9BDB0"}}}]}}},
    }
    save(report / "StaticResources" / "RegisteredResources" / "SpotifyTrackerTheme.json", theme)
    save(definition / "report.json", {"$schema": BASE + "item/report/definition/report/1.0.0/schema.json", "layoutOptimization": "None", "themeCollection": {"customTheme": {"name": "SpotifyTrackerTheme", "reportVersionAtImport": "1.0.0", "type": "RegisteredResources"}}, "resourcePackages": [{"name": "RegisteredResources", "type": "RegisteredResources", "items": [{"name": "SpotifyTrackerTheme", "path": "SpotifyTrackerTheme.json", "type": "CustomTheme"}]}]})

    pages = []
    def page(name, title, subtitle, cards):
        pages.append(name)
        path = definition / "pages" / name
        save(path / "page.json", {"$schema": BASE + "item/report/definition/page/1.0.0/schema.json", "name": name, "displayName": title, "displayOption": "FitToPage", "height": 900, "width": 1280, "objects": {"background": [{"properties": {"color": color("#0D1712"), "transparency": literal(0)}}]}})
        visuals = [textbox(name + "_heading", title, subtitle)]
        visuals += [chart(name + "_year", "slicer", "Year", 32, 112, 260, 72, {"Values": [("Dates", "year", False)]}), chart(name + "_artist", "slicer", "Artist", 310, 112, 500, 72, {"Values": [("Artists", "artist_name", False)]}), chart(name + "_stream", "slicer", "Audio / video", 828, 112, 420, 72, {"Values": [("Listening", "stream_type", False)]})]
        for i, measure in enumerate(cards):
            w = (1216 - 16 * (len(cards) - 1)) // len(cards)
            visuals.append(chart(name + f"_card_{i}", "card", measure, 32 + i * (w + 16), 202, w, 110, {"Values": [("Listening", measure, True)]}))
        return path, visuals

    path, visuals = page("overview", "Your listening, over time", "MUSIC HISTORY  |  Playback-end dates in configured timezone  |  Hours include short plays; other KPIs use qualifying plays", ["Listening Hours", "Qualified Plays", "Unique Artists", "Unique Songs", "Discovery Rate"])
    visuals += [
        chart("monthly_hours", "lineChart", "Listening hours by month", 32, 330, 760, 250, {"Category": [("Dates", "month", False)], "Y": [("Listening", "Listening Hours", True)]}, ("Dates", "month", False, "Ascending")),
        chart("top_artists", "clusteredBarChart", "Top artists · listening hours", 810, 330, 438, 528, {"Category": [("Artists", "artist_name", False)], "Y": [("Listening", "Top Artist Hours", True)]}, ("Listening", "Top Artist Hours", True, "Descending")),
        chart("top_tracks", "clusteredBarChart", "Top songs · qualified plays", 32, 598, 760, 260, {"Category": [("Tracks", "track_label", False)], "Y": [("Listening", "Top Song Plays", True)]}, ("Listening", "Top Song Plays", True, "Descending")),
    ]
    for v in visuals: save(path / "visuals" / v["name"] / "visual.json", v)

    path, visuals = page("discovery", "Discovery & artist loyalty", "Discovery = first observed qualifying song play  |  Loyalty = plays to artists heard on an earlier local date  |  Boundary periods may be partial", ["Discovery Rate", "New Songs", "New Artists", "Artist Loyalty", "Top 5 Artist Share"])
    visuals += [
        chart("discovery_trend", "lineChart", "Song discovery rate by month", 32, 330, 598, 250, {"Category": [("Dates", "month", False)], "Y": [("Listening", "Discovery Rate", True)]}, ("Dates", "month", False, "Ascending")),
        chart("loyalty_trend", "lineChart", "Artist loyalty by month", 648, 330, 600, 250, {"Category": [("Dates", "month", False)], "Y": [("Listening", "Artist Loyalty", True)]}, ("Dates", "month", False, "Ascending")),
        chart("new_artists", "clusteredColumnChart", "First-observed artists by year", 32, 598, 598, 260, {"Category": [("Dates", "year", False)], "Y": [("Listening", "New Artists", True)]}, ("Dates", "year", False, "Ascending")),
        chart("new_songs", "clusteredColumnChart", "First-observed songs by year", 648, 598, 600, 260, {"Category": [("Dates", "year", False)], "Y": [("Listening", "New Songs", True)]}, ("Dates", "year", False, "Ascending")),
    ]
    for v in visuals: save(path / "visuals" / v["name"] / "visual.json", v)

    path, visuals = page("habits_genres", "Listening habits & genres", "Unknown genres remain visible until verified mappings are added  |  Genre coverage reports mapped listening time  |  No genres are inferred from titles", ["Average Minutes per Day", "Known Skip Rate", "Genre Coverage", "Artist Loyalty"])
    visuals += [
        chart("weekday_pattern", "clusteredColumnChart", "Average minutes per calendar day", 32, 330, 598, 250, {"Category": [("Dates", "weekday", False)], "Y": [("Listening", "Average Minutes per Day", True)]}),
        chart("hour_pattern", "clusteredColumnChart", "Listening hours by local playback-end hour", 648, 330, 600, 250, {"Category": [("Listening", "hour", False)], "Y": [("Listening", "Listening Hours", True)]}, ("Listening", "hour", False, "Ascending")),
        chart("genre_distribution", "clusteredBarChart", "Listening hours by primary artist genre", 32, 598, 598, 260, {"Category": [("Artists", "primary_genre", False)], "Y": [("Listening", "Listening Hours", True)]}, ("Listening", "Listening Hours", True, "Descending")),
        chart("genre_trend", "stackedAreaChart", "Genre listening over time · hours", 648, 598, 600, 260, {"Category": [("Dates", "month", False)], "Series": [("Artists", "primary_genre", False)], "Y": [("Listening", "Listening Hours", True)]}, ("Dates", "month", False, "Ascending")),
    ]
    for v in visuals: save(path / "visuals" / v["name"] / "visual.json", v)
    save(definition / "pages" / "pages.json", {"$schema": BASE + "item/report/definition/pagesMetadata/1.0.0/schema.json", "pageOrder": pages, "activePageName": "overview"})
    dax = "\n\n".join(f"// {desc}\n{n} =\n    {expr}" for n, expr, _, desc in MEASURES)
    (folder / "measures.dax").write_text(dax + "\n", encoding="utf-8", newline="\n")
