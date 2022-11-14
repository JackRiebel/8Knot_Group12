from dash import html, dcc
import dash
import dash_bootstrap_components as dbc
from dash import callback
from dash.dependencies import Input, Output, State
import plotly.graph_objects as go
import pandas as pd
import datetime as dt
import logging
from dateutil.relativedelta import *  # type: ignore
import plotly.express as px
from pages.utils.graph_utils import get_graph_time_values

from pages.utils.job_utils import handle_job_state, nodata_graph
from queries.issues_query import issues_query as iq
from app import jm
import time

gc_issue_staleness = dbc.Card(
    [
        dbc.CardBody(
            [
                dcc.Interval(
                    id="issue-staleness-timer",
                    disabled=False,
                    n_intervals=1,
                    max_intervals=1,
                    interval=1500,
                ),
                html.H4(
                    "Issue Activity- Staleness",
                    className="card-title",
                    style={"text-align": "center"},
                ),
                dbc.Popover(
                    [
                        dbc.PopoverHeader("Graph Info:"),
                        dbc.PopoverBody(
                            "This visualization shows how many issues have been open different buckets of time.\n\
                            It can tell you if there are issues that are staying idly open."
                        ),
                    ],
                    id="overview-popover-is",
                    target="overview-popover-target-is",  # needs to be the same as dbc.Button id
                    placement="top",
                    is_open=False,
                ),
                dcc.Graph(id="issue_staleness"),
                dbc.Form(
                    [
                        dbc.Row(
                            [
                                dbc.Label(
                                    "Date Interval:",
                                    html_for="issue-staleness-interval",
                                    width="auto",
                                    style={"font-weight": "bold"},
                                ),
                                dbc.Col(
                                    [
                                        dbc.RadioItems(
                                            id="issue-staleness-interval",
                                            options=[
                                                {"label": "Trend", "value": "D"},
                                                {"label": "Month", "value": "M"},
                                                {"label": "Year", "value": "Y"},
                                            ],
                                            value="M",
                                            inline=True,
                                        ),
                                    ]
                                ),
                                dbc.Col(
                                    dbc.Button(
                                        "About Graph",
                                        id="overview-popover-target-is",
                                        color="secondary",
                                        size="sm",
                                    ),
                                    width="auto",
                                    style={"padding-top": ".5em"},
                                ),
                            ],
                            align="center",
                        ),
                        dbc.Row(
                            [
                                dbc.Label(
                                    "Days Until Staling:",
                                    html_for="i_staling_days",
                                    width={"size": "auto"},
                                    style={"font-weight": "bold"},
                                ),
                                dbc.Col(
                                    dbc.Input(
                                        id="i_staling_days",
                                        type="number",
                                        min=1,
                                        max=120,
                                        step=1,
                                        value=7,
                                    ),
                                    className="me-2",
                                    width=2,
                                ),
                                dbc.Label(
                                    "Days Until Stale:",
                                    html_for="i_stale_days",
                                    width={"size": "auto"},
                                    style={"font-weight": "bold"},
                                ),
                                dbc.Col(
                                    dbc.Input(
                                        id="i_stale_days",
                                        type="number",
                                        min=1,
                                        max=120,
                                        step=1,
                                        value=30,
                                    ),
                                    className="me-2",
                                    width=2,
                                ),
                            ],
                            align="center",
                        ),
                        dbc.Alert(
                            children="Please ensure that 'Days Until Staling' is less than 'Days Until Stale'",
                            id="issue_staling_stale_check_alert",
                            dismissable=True,
                            fade=False,
                            is_open=False,
                            color="warning",
                        ),
                    ]
                ),
            ]
        )
    ],
    color="light",
)


@callback(
    Output("overview-popover-is", "is_open"),
    [Input("overview-popover-target-is", "n_clicks")],
    [State("overview-popover-is", "is_open")],
)
def toggle_popover_issues(n, is_open):
    if n:
        return not is_open
    return is_open


@callback(
    Output("issue_staleness", "figure"),
    Output("issue_staling_stale_check_alert", "is_open"),
    Output("issue-staleness-timer", "n_intervals"),
    [
        Input("repo-choices", "data"),
        Input("issue-staleness-timer", "n_intervals"),
        Input("issue-staleness-interval", "value"),
        Input("i_staling_days", "value"),
        Input("i_stale_days", "value"),
    ],
)
def new_staling_issues(repolist, timer_pings, interval, staling_interval, stale_interval):
    logging.debug("IS - PONG")

    if staling_interval > stale_interval:
        return dash.no_update, True, dash.no_update

    if staling_interval is None or stale_interval is None:
        return dash.no_update, dash.no_update, dash.no_update

    ready, results, graph_update, interval_update = handle_job_state(jm, iq, repolist)
    if not ready:
        return graph_update, dash.no_update, interval_update

    logging.debug("ISSUE_STALENESS_VIZ - START")
    start = time.perf_counter()

    # create dataframe from record data
    df = pd.DataFrame(results)

    # test if there is data
    if df.empty:
        logging.debug("ISSUE STALENESS - NO DATA AVAILABLE")
        return nodata_graph, False, dash.no_update

    # convert to datetime objects rather than strings
    df["created"] = pd.to_datetime(df["created"], utc=True)
    df["closed"] = pd.to_datetime(df["closed"], utc=True)

    # order values chronologically by creation date
    df = df.sort_values(by="created", axis=0, ascending=True)

    # first and last elements of the dataframe are the
    # earliest and latest events respectively
    earliest = df["created"].min()
    latest = max(df["created"].max(), df["closed"].max())

    # generating buckets beginning to the end of time by the specified interval
    dates = pd.date_range(start=earliest, end=latest, freq=interval, inclusive="both")

    # df for new, staling, and stale issues for time interval
    df_status = dates.to_frame(index=False, name="Date")

    df_status["New"], df_status["Staling"], df_status["Stale"] = zip(
        *df_status.apply(
            lambda row: get_new_staling_stale_up_to(df, row.Date, staling_interval, stale_interval), axis=1
        )
    )

    if interval == "M":
        df_status["Date"] = df_status["Date"].dt.strftime("%Y-%m")
    elif interval == "Y":
        df_status["Date"] = df_status["Date"].dt.year

    # time values for graph
    x_r, x_name, hover, period = get_graph_time_values(interval)

    # making a line graph if the bin-size is small enough.
    if interval == "D":
        fig = go.Figure(
            [
                go.Scatter(
                    name="New",
                    x=df_status["Date"],
                    y=df_status["New"],
                    mode="lines",
                    showlegend=True,
                    hovertemplate="Issues New: %{y}<br>%{x|%b %d, %Y} <extra></extra>",
                ),
                go.Scatter(
                    name="Staling",
                    x=df_status["Date"],
                    y=df_status["Staling"],
                    mode="lines",
                    showlegend=True,
                    hovertemplate="Issues Staling: %{y}<br>%{x|%b %d, %Y} <extra></extra>",
                ),
                go.Scatter(
                    name="Stale",
                    x=df_status["Date"],
                    y=df_status["Stale"],
                    mode="lines",
                    showlegend=True,
                    hovertemplate="Issues Stale: %{y}<br>%{x|%b %d, %Y} <extra></extra>",
                ),
            ]
        )
    else:
        fig = px.bar(
            df_status,
            x="Date",
            y=["New", "Staling", "Stale"],
        )

        # edit hover values
        fig.update_traces(hovertemplate=hover + "<br>Issues: %{y}<br>" + "<extra></extra>")

    fig.update_layout(xaxis_title="Time", yaxis_title="Issues", legend_title="Type")

    logging.debug(f"ISSUE_STALENESS_VIZ - END - {time.perf_counter() - start}")
    return fig, False, dash.no_update


def get_new_staling_stale_up_to(df, date, staling_interval, stale_interval):

    # drop rows that are more recent than the date limit
    df_created = df[df["created"] <= date]

    # drop rows that have been closed before date
    df_in_range = df_created[df_created["closed"] > date]

    # include rows that have a null closed value
    df_in_range = pd.concat([df_in_range, df_created[df_created.closed.isnull()]])

    # time difference for the amount of days before the threshold date
    staling_days = date - relativedelta(days=+staling_interval)

    # time difference for the amount of days before the threshold date
    stale_days = date - relativedelta(days=+stale_interval)

    # issuess still open at the specified date
    numTotal = df_in_range.shape[0]

    # num of currently open issues that have been create in the last staling_value amount of days
    numNew = df_in_range[df_in_range["created"] >= staling_days].shape[0]

    staling = df_in_range[df_in_range["created"] > stale_days]
    numStaling = staling[staling["created"] < staling_days].shape[0]

    numStale = numTotal - (numNew + numStaling)

    return [numNew, numStaling, numStale]
