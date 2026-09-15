#!/usr/bin/env Rscript
# Usage: Rscript scripts/monthly_patient_graphics.R PROCESSED_RUN_DIR OUTPUT_ROOT DEMOGRAPHICS_CSV
suppressPackageStartupMessages({
  library(ggplot2)
  library(dplyr)
  library(tidyr)
  library(readr)
  library(jsonlite)
  library(patchwork)
})

read_table <- function(path) {
  read_csv(path, col_types = cols(.default = col_character()), show_col_types = FALSE)
}

# Draw a vector page so the rounded tracks and labels remain sharp in PDF.
make_patient_month <- function(days, counts, patient, month, palette, run_id, patient_name = patient) {
  first <- as.Date(paste0(month, "-01"))
  last <- seq(first, by = "month", length.out = 2)[2] - 1
  calendar <- tibble(date = seq(first, last, by = "day")) |>
    left_join(filter(days, participant_id == patient), by = "date") |>
    mutate(day = as.integer(format(date, "%d")))
  events <- counts |> filter(participant_id == patient, date >= first, date <= last) |>
    mutate(day = as.integer(format(date, "%d")))
  totals <- events |> group_by(day) |> summarise(n = sum(n), .groups = "drop")
  max_count <- max(c(1, totals$n))
  types <- sort(unique(events$seizure_type))
  grobs <- list()
  add <- function(g) grobs[[length(grobs) + 1L]] <<- g
  label <- function(text, x, y, color = "#222222", size = 12, just = "centre", face = "plain") {
    add(grid::textGrob(text, x, y, just = just,
        gp = grid::gpar(col = color, fontsize = size, fontfamily = "sans", fontface = face)))
  }
  label(tools::toTitleCase(tolower(patient_name)), .05, .965, size = 20, just = "left", face = "bold")
  label(format(first, "%B %Y"), .95, .965, size = 17, just = "right")
  # The segment polygons follow the same rounded silhouette as the gray track.
  # Vertical heights remain proportional to counts, including tiny segments.
  segment <- function(x, bottom, height, low, high, color) {
    width <- .039
    radius <- .009
    ys <- sort(unique(c(low, high, seq(low, high, length.out = 80))))
    inset <- ifelse(ys < radius, radius - sqrt(pmax(0, radius^2 - (ys-radius)^2)),
      ifelse(ys > height-radius, radius - sqrt(pmax(0, radius^2-(ys-(height-radius))^2)), 0))
    add(grid::polygonGrob(x = c(x-width/2+inset, rev(x+width/2-inset)),
                         y = bottom + c(ys, rev(ys)),
                         gp = grid::gpar(fill = color, col = NA)))
  }
  for (half in 1:2) {
    dates <- if (half == 1) 1:15 else 16:nrow(calendar)
    bottom <- if (half == 1) .705 else .315
    height <- .215
    label("Day", .05, bottom-.026, size = 10, color = "#777777", just = "left")
    label("Seizures", .05, bottom-.057, size = 10, color = "#63279B", just = "left")
    label("Sleep", .05, bottom-.092, size = 10, color = "#777777", just = "left")
    label("Impact", .05, bottom-.124, size = 10, color = "#777777", just = "left")
    for (j in seq_along(dates)) {
      day <- dates[j]
      x <- .145 + (j-1)*.052
      row <- filter(calendar, .data$day == .env$day)
      segment(x, bottom, height, 0, height, "#EBEBEB")
      daily <- filter(events, .data$day == .env$day) |> arrange(seizure_type)
      accumulated <- 0
      for (k in seq_len(nrow(daily))) {
        h <- daily$n[k]/max_count*height
        segment(x, bottom, height, accumulated, accumulated+h, palette[[daily$seizure_type[k]]])
        rgb <- as.numeric(grDevices::col2rgb(palette[[daily$seizure_type[k]]]))/255
        linear <- ifelse(rgb <= .04045, rgb/12.92, ((rgb+.055)/1.055)^2.4)
        luminance <- sum(linear*c(.2126, .7152, .0722))
        ink <- if (luminance > .179) "#222222" else "#FFFFFF"
        if (h >= .018) label(daily$n[k], x, bottom+accumulated+h/2, ink, 11)
        accumulated <- accumulated+h
      }
      total <- sum(daily$n)
      status <- row$seizure_status
      total_label <- if (total > 0) as.character(total) else if (is.na(status)) "—" else
        switch(status, confirmed_none = "0", "—")
      label(day, x, bottom-.026, size = 13)
      label(total_label, x, bottom-.057, color = "#63279B", size = 13)
      # Amma traffic-light scales; missing optional values remain dashes.
      sleep_color <- c(poor = "#FF0000", fair = "#FFC000", good = "#008000")[row$sleep_quality]
      impact_color <- c(red = "#FF0000", yellow = "#FFC000", green = "#008000")[row$mood_rating]
      indicator <- function(color, y) {
        if (is.na(color)) label("—", x, y, color = "#666666", size = 11) else
          add(grid::circleGrob(x, y, r = grid::unit(1.8, "mm"),
                              gp = grid::gpar(fill = unname(color), col = NA)))
      }
      indicator(sleep_color, bottom-.092)
      indicator(impact_color, bottom-.124)
    }
  }
  # Two-column legend uses reference-style colored circles and matching text.
  legend_rows <- max(1, ceiling(length(types)/2))
  for (i in seq_along(types)) {
    x <- if (i %% 2 == 1) .055 else .525
    y <- .13 - ((i-1) %/% 2)*min(.032, .09/max(1, legend_rows-1))
    add(grid::circleGrob(x, y, r = grid::unit(2.1, "mm"),
                        gp = grid::gpar(fill = palette[[types[i]]], col = NA)))
    label(gsub("_", " ", types[i]), x+.022, y, color = palette[[types[i]]], size = 10.5, just = "left")
  }
  # Crop unused space below the last legend row and scale the composition to
  # the PDF page. A short legend therefore does not leave an empty footer.
  last_legend_y <- if (length(types)) .13 - (legend_rows-1)*min(.032, .09/max(1, legend_rows-1)) else .191
  lower_edge <- last_legend_y - .025
  content <- grid::gTree(children = do.call(grid::gList, grobs),
    vp = grid::viewport(y = -lower_edge/(1-lower_edge), just = c("centre", "bottom"),
                        height = 1/(1-lower_edge)))
  content
}

render_monthly_reports <- function(run_dir, output_root, demographics_csv) {
  manifest <- fromJSON(file.path(run_dir, "run_manifest.json"))
  if (!identical(manifest$status, "passed")) stop("Source run did not pass QC")
  source_run_id <- manifest$run_id
  if (is.null(source_run_id) || length(source_run_id) != 1 || !nzchar(source_run_id))
    stop("Source run manifest has no run_id")
  days <- read_table(file.path(run_dir, "participant_days.csv")) |>
    mutate(date = as.Date(date), sleep_quality = tolower(sleep_quality))
  events <- read_table(file.path(run_dir, "seizure_events.csv")) |>
    mutate(date = as.Date(observation_date),
           seizure_type = coalesce(seizure_type, "Unspecified type"))
  if (anyNA(days$date) || anyNA(events$date)) stop("Invalid source dates")
  if (anyDuplicated(days[c("participant_id", "date")])) stop("Duplicate participant days")
  # Count every source observation, including identical reported seizure rows.
  counts <- events |> count(participant_id, date, seizure_type, name = "n")
  totals <- counts |> group_by(participant_id, date) |>
    summarise(observed = sum(n), .groups = "drop")
  if (nrow(anti_join(totals, days, by = c("participant_id", "date"))) > 0)
    stop("Seizure events without matching diary days")
  reconciled <- days |> left_join(totals, by = c("participant_id", "date"))
  if (any(as.numeric(reconciled$seizure_count) != coalesce(reconciled$observed, 0), na.rm = TRUE))
    stop("Seizure event counts do not reconcile with participant_days")
  demographics <- read_table(demographics_csv)
  if (!all(c("Participant ID", "fname", "lname") %in% names(demographics)))
    stop("Demographics CSV requires Participant ID, fname, and lname")
  names_table <- demographics |>
    transmute(participant_id = recode(trimws(`Participant ID`), "SCN8A-0010" = "SCN8A-010"),
              patient_name = trimws(paste(coalesce(fname, ""), coalesce(lname, "")))) |>
    filter(participant_id %in% days$participant_id)
  if (anyDuplicated(names_table$participant_id)) stop("Duplicate participant IDs in demographics")
  if (any(!unique(days$participant_id) %in% names_table$participant_id) ||
      any(names_table$patient_name == ""))
    stop("Every report participant must have a usable name in demographics")
  if (any(grepl("[[:cntrl:]/\\\\:*?\"<>|]", names_table$participant_id)) ||
      any(names_table$participant_id %in% c("", ".", "..")))
    stop("Participant IDs must be safe for report paths")
  dir.create(output_root, recursive = TRUE, showWarnings = FALSE)
  # Exact hex values from the user-provided EPI4-Color_Palette.png.
  epi4_colors <- c("#00545E", "#A30234", "#677719", "#0076C0", "#7A5071",
                   "#E37C1D", "#002157", "#5698A3", "#CE8080", "#ABB47D",
                   "#F1B682", "#FFDE75", "#A1C5CB", "#BACFEC", "#E4B8B4",
                   "#511C23", "#002E30")
  fixed_colors <- c("Absence" = "#00545E", "Focal (no observable sign)" = "#A30234",
                    "Focal Motor" = "#677719", "Tonic" = "#0076C0", "Tonic-Clonic" = "#7A5071")
  all_types <- sort(unique(counts$seizure_type))
  assigned <- setNames(rep(NA_character_, length(all_types)), all_types)
  known <- intersect(all_types, names(fixed_colors))
  assigned[known] <- fixed_colors[known]
  for (type in setdiff(all_types, known)) {
    available <- setdiff(epi4_colors, c(unname(fixed_colors), unname(assigned)))
    if (!length(available)) stop("Epilepsia Open palette exhausted; add a non-color encoding")
    assigned[type] <- available[1]
  }
  palette <- assigned
  jobs <- days |> transmute(participant_id, month = format(date, "%Y-%m")) |> distinct()
  report_run_dir <- file.path(output_root, source_run_id)
  if (dir.exists(report_run_dir)) stop("Report output already exists for this source run")
  dir.create(report_run_dir, recursive = TRUE, showWarnings = FALSE)
  index <- list()
  for (i in seq_len(nrow(jobs))) {
    patient <- jobs$participant_id[i]
    identity <- filter(names_table, participant_id == patient)
    month <- jobs$month[i]
    destination <- file.path(report_run_dir, patient)
    dir.create(destination, recursive = TRUE, showWarnings = FALSE)
    path <- file.path(destination, paste0(patient, "_", month, ".pdf"))
    plot <- make_patient_month(days, counts, patient, month, palette, source_run_id, identity$patient_name)
    ggsave(path, plot, width = 12, height = 10, device = grDevices::cairo_pdf, bg = "white", limitsize = TRUE)
    relative_file <- gsub("\\\\", "/", file.path(patient, basename(path)))
    index[[i]] <- tibble(participant_id = patient, month, source_run = source_run_id,
                         relative_file, file = path)
  }
  output_index <- bind_rows(index)
  if (!nrow(output_index)) stop("No patient-month reports were generated")
  box_index <- output_index |> select(-file)
  write_csv(box_index, file.path(report_run_dir, "report_index.csv"))
  report_manifest <- list(
    status = "passed",
    report_schema_version = "1.0.0",
    generated_utc = format(Sys.time(), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC"),
    source_run_id = source_run_id,
    source_pipeline_version = if (is.null(manifest$pipeline_version)) NA_character_ else manifest$pipeline_version,
    report_count = nrow(box_index),
    reports = box_index
  )
  write_json(report_manifest, file.path(report_run_dir, "report_manifest.json"),
             pretty = TRUE, auto_unbox = TRUE, na = "null")
  message("Generated ", nrow(jobs), " patient-month graphics")
  invisible(output_index)
}

if (sys.nframe() == 0L) {
  args <- commandArgs(trailingOnly = TRUE)
  if (length(args) != 3)
    stop("Usage: monthly_patient_graphics.R PROCESSED_RUN_DIR OUTPUT_ROOT DEMOGRAPHICS_CSV")
  render_monthly_reports(
    args[1], args[2], args[3])
}
