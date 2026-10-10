import pandas as pd


def _mean_absolute_error(values, growth, years, year_columns):
    differences = []
    for index in range(1, len(years)):
        year = years[index]
        previous_year = years[index - 1]
        current_value = values[year_columns[year]]
        previous_value = values[year_columns[previous_year]]
        reported_growth = growth[year_columns[year]]
        if (
            pd.notna(current_value)
            and pd.notna(previous_value)
            and previous_value != 0
            and pd.notna(reported_growth)
        ):
            calculated_growth = (current_value / previous_value - 1) * 100
            differences.append(abs(calculated_growth - reported_growth))
    return (
        float(pd.Series(differences).median())
        if len(differences) >= 2
        else None
    )


def normalize_statement_year_order(statement, ratios, years, ticker=None):
    """Reverse annual values for a statement only when its growth ratios prove it."""
    if statement is None or statement.empty or ratios is None or ratios.empty:
        return statement.copy() if statement is not None else pd.DataFrame()

    statement = statement.copy()
    years = [str(year) for year in years]
    statement_columns = {str(column): column for column in statement.columns}
    ratio_columns = {str(column): column for column in ratios.columns}
    shared_years = [
        year
        for year in years
        if year in statement_columns and year in ratio_columns
    ]
    if len(shared_years) < 3 or "item_id" not in statement or "item_id" not in ratios:
        return statement

    statement_mask = pd.Series(True, index=statement.index)
    ratio_mask = pd.Series(True, index=ratios.index)
    if ticker is not None:
        if "ticker" in statement.columns:
            statement_mask &= (
                statement["ticker"]
                .astype("string")
                .str.strip()
                .str.upper()
                .eq(str(ticker).strip().upper())
            )
        if "ticker" in ratios.columns:
            ratio_mask &= (
                ratios["ticker"]
                .astype("string")
                .str.strip()
                .str.upper()
                .eq(str(ticker).strip().upper())
            )

    statement_rows = statement.loc[statement_mask]
    ratio_rows = ratios.loc[ratio_mask]
    current_errors = []
    reversed_errors = []
    for item_id in ratio_rows["item_id"].dropna().unique():
        matching_statement = statement_rows.loc[
            statement_rows["item_id"].eq(item_id)
        ]
        matching_ratio = ratio_rows.loc[ratio_rows["item_id"].eq(item_id)]
        if matching_statement.empty or matching_ratio.empty:
            continue

        numeric_values = matching_statement[[
            statement_columns[year] for year in shared_years
        ]].apply(pd.to_numeric, errors="coerce")
        populated = numeric_values.notna().any(axis=1)
        if not populated.any():
            continue
        values = numeric_values.loc[populated].iloc[-1]

        numeric_growth = matching_ratio[[
            ratio_columns[year] for year in shared_years
        ]].apply(pd.to_numeric, errors="coerce")
        populated_growth = numeric_growth.notna().any(axis=1)
        if not populated_growth.any():
            continue
        growth = numeric_growth.loc[populated_growth].iloc[-1]

        aligned_growth = growth.rename(
            index={ratio_columns[year]: statement_columns[year] for year in shared_years}
        )
        errors = (
            _mean_absolute_error(
                values, aligned_growth, shared_years, statement_columns
            ),
            _mean_absolute_error(
                values.iloc[::-1].set_axis(values.index),
                aligned_growth,
                shared_years,
                statement_columns,
            ),
        )
        if errors[0] is not None and errors[1] is not None:
            current_errors.append(errors[0])
            reversed_errors.append(errors[1])

    if (
        current_errors
        and reversed_errors
        and sum(reversed_errors) / len(reversed_errors)
        < sum(current_errors) / len(current_errors)
    ):
        target_index = statement.index[statement_mask]
        source_columns = [statement_columns[year] for year in shared_years[::-1]]
        target_columns = [statement_columns[year] for year in shared_years]
        statement.loc[target_index, target_columns] = statement.loc[
            target_index, source_columns
        ].to_numpy(copy=True)

    return statement
