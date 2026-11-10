# P4 Insights: independent review and macOS UI check

Date: 2026-11-09 (review), 2026-11-10 (fixes). Scope: P4 as merged in PR #110
(`f10618c`): the Insights page, its editors and dialogs, the chart component and the
services and domain behind them, read line by line and then exercised on macOS,
offscreen and on native Cocoa, with a synthetic profile. It follows the two author
reviews ([backend](p4-backend-code-review.md), [dashboard](p4-dashboard-code-review.md)).
It is not owner acceptance, and no owner profile was opened or migrated.

## Findings and resolutions

Every finding was reproduced on the merged code before it was fixed. The regressions are
in `tests/ui/test_insights_qa.py` unless another file is named.

| ID | Severity | Finding | Resolution and regression |
| --- | --- | --- | --- |
| U1 | High | Archive / reactivate in Manage metrics always failed. The desktop passed the saved `MetricDefinition`, and `configure()` revalidated all of its fields as a `MetricDefinitionSpec`, which forbids the extra ones. The service test passed because it used a bare spec. | `define()` and `configure()` revalidate a spec from its own five fields. The button reads Archive metric or Reactivate metric. `test_archive_and_reactivate_work_from_the_manager`, and `test_a_saved_definition_is_archived_and_reactivated_as_it_was_read` in the service tests. |
| U2 | High | Every refresh, including the one-minute timer, deleted and rebuilt all cards: keyboard focus jumped to the first date field, an open data table closed, the selected value reset and the page scrolled. | A refresh that changes neither the layout, the mode nor which widgets have a value puts the fresh values into the cards on screen (`MetricChartCard.set_display`). New cards are built only for a new layout or mode. `test_a_refresh_updates_the_cards_where_they_are`; `test_fresh_values_arrive_in_place_and_keep_the_owners_place` in the chart tests. |
| U3 | Medium | A Planned workload widget could not stay valid. It needs a range from today on, the dashboard's dates begin six days back, and a widget's own dates are fixed, so it was unavailable at once or from the next day. | `resolved_widget_query` drops the part of a forward plan's range before today, in the range's zone, for the dashboard and the preview alike. Every widget of one refresh is queried at one instant. A range wholly past says "Planned workload covers today and later". `test_planned_workload_starts_today_on_the_dashboard_range`; `test_a_forward_plan_starts_today_and_other_metrics_keep_their_dates` in the domain tests. |
| U4 | Medium | A From date after Through, or more than 366 dates, raised inside `dates()`: the page showed "could not load or save", and Add widget raised in its click handler and did nothing. | The two fields keep an ordered range of at most 366 dates: moving one end moves the other (`keep_range`), on the page and in the widget editor. `test_the_date_fields_always_hold_a_range_the_queries_accept`. |
| U5 | Medium | A dialog's status line was the first item inside its scrolled fields, and the buttons were at the bottom, so a refused action showed nothing on screen. Refusals were generic, and Save correction, Delete, Restore and Undo were enabled with nothing to act on. | The status line sits under the scrolled fields. Refusals use the app's own reasons (`InputRefused`, `refusal_text`), the same for Preview and Save. Entry buttons follow the chosen metric and observation. Manage metrics opens tall enough to show Log new value where the screen allows. `test_what_a_button_answers_is_shown_outside_the_scrolled_fields`, `test_preview_and_save_refuse_the_same_input_in_the_same_words`, `test_entry_buttons_follow_the_chosen_metric_and_observation`. |
| U6 | Medium | Return or Enter on a row of Contributing records did nothing on macOS: the table relied on Qt's `itemActivated`, which macOS does not send for those keys. D11 fixed the chart's data table only. | `RecordTable` activates the current row once on every platform, as `ActivatingList` does. `test_return_or_enter_opens_a_contributing_record_once`. |
| U7 | Medium | On macOS Tab skipped all twelve Insights combo boxes unless Full Keyboard Access was on, and stopped on scroll areas. | `combo()` gives each `StrongFocus`, as the app's other forms do; the page's and the dialogs' scroll areas take no focus. `test_every_combo_box_takes_tab_and_no_scroll_area_does`. |
| U8 | Medium | A widget saved with a blank title kept the old metric's name when its metric was changed. | A title equal to its metric's name is shown blank again, with the metric's name as the placeholder, so it follows the metric. A title the owner wrote is kept. `test_a_blank_title_follows_the_metric`. |
| U9 | Medium | Manage metrics listed the oldest 100 observations first, with forward-only paging, so recent values and a value just logged were not shown. | `entries(newest_first=True)` for the owner's list; the default order is unchanged for other readers. `test_the_observation_list_opens_on_the_newest`; `test_newest_first_pages_run_back_from_the_latest_date` in the service tests. |
| U10 | Low | Undo removal / reset stayed visible after a later layout change and could then only fail. | Any saved layout change withdraws an earlier undo, and a render of another revision drops it. `test_undo_is_withdrawn_once_the_layout_moves_on`. |
| U11 | Medium | Axis ticks were raw quarter-steps of the data range printed with three significant digits: `9.65e+03` for 9,650, labels that did not match their gridlines, fractional and negative ticks for counts. Category bars were named only at the first and last bar. | Round ticks (`nice_ticks`), labels in plain digits (`axis_label`), whole steps for whole values, no dip below zero for a flat line. Every category that has room is named; dates stay whole and are thinned (`label_marks`, `bottom_labels`). Chart tests: `test_axis_ticks_are_round_values_in_plain_digits`, `test_labels_under_the_plot_never_touch_and_always_name_the_last`, `test_every_category_is_named_and_dates_are_never_cut`. |
| U12 | Medium | The two data tables were the app's first tables and had no theme style: a selected row's text measured 2.5:1 against its fill in light mode, a header was clipped, and Contributing records gave its explanation column 100 px. | A `dataTable` style in the theme (panel, hairline grid, `selection` fill with `text`). Columns fit their content, the explanation takes the rest and wraps, the empty "About this value" column is hidden. `test_a_selected_table_row_is_readable` (4.5:1 in both modes) and `test_the_about_column_shows_only_when_a_value_has_something_to_say` in the chart tests. |
| U13 | Medium | Each save disabled the dialog and never gave focus back, and Return in the Value field did not log, so every value needed the mouse. | A write returns focus to where it was, or to the Value field after a logged value or a new metric. Return in the Value field logs. `test_logging_keeps_the_keyboard_in_the_value_field`. |
| U14 | Medium | A card's accessible name was `Widget <id>`, and its Contributing records, Edit, Earlier, Later, Size and Remove buttons had the same name on every card. | Cards and their buttons are named for their widget's title. `test_cards_and_their_buttons_are_named_for_their_widget`. |
| U15 | Medium | A dashboard project or life-area filter was applied to every widget, so metrics that cannot take it (time metrics; custom metrics for a life area) turned to "Unavailable", and one refused widget made the shared read fail for all. | The resolver applies a dashboard filter only where the metric can take it. The others keep their whole value and say in their caption that the filter does not apply. Query validation is unchanged: a filter saved on a widget is still refused where it is unsupported. `test_a_dashboard_filter_leaves_metrics_it_cannot_reach_whole`; `test_a_dashboard_filter_reaches_only_a_metric_that_can_take_it` in the domain tests. |

## Noted, not changed

- Wide and Full widgets render alike (both span the page), so one step of Size shows no change.
- Insights shares the bookmark icon with Projects and Saved mail, and its header subtitle
  is the fallback "Your daily workspace".
- A card's caption shows its refresh time as a UTC ISO timestamp.
- The toolbar's date fields have no visible labels, and its controls fill four rows.
- Metrics are listed in identifier order, not by name or creation.
- One click on a chart opens Contributing records; there is no select-only click.
- Ticking Edit layout re-runs every query; a compared widget is computed twice per
  refresh; the minute timer recomputes the whole dashboard while Insights is the page shown.

## Verification and limits

Results of the final runs are in [the P4 register](p4-insights.md#independent-review-and-macos-ui-check).
Offscreen focus is asserted through each window's focus widget, which does not depend on
the desktop giving a test window focus. VoiceOver, a real display at a fractional scale,
Windows and the owner's own records were not checked here. Large-profile latency is as
recorded in the register.
