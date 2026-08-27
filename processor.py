import app


def main():
    print("Starting automated plumbing invoice processing...")

    # Get all PDFs and skip ones already processed
    all_pdfs = app.get_drive_pdfs()
    processed_ids = app.get_processed_file_ids()

    new_pdfs = [
        file
        for file in all_pdfs
        if file["id"] not in processed_ids
    ]

    print(f"Found {len(new_pdfs)} new invoice(s).")

    processed_count = 0
    failed_count = 0

    # Process every new invoice
    for selected_file in new_pdfs:
        try:
            print(f"Processing: {selected_file['name']}")

            app.process_invoice_file(
                selected_file
            )

            processed_count += 1

        except Exception as error:
            failed_count += 1
            print(
                f"FAILED: {selected_file['name']} — {error}"
            )

    # Standardize all remaining existing line items
    while True:
        try:
            updated, remaining, comparison_count = (
                app.backfill_existing_line_items(
                    limit=50
                )
            )

            print(
                f"Standardized {updated} item(s). "
                f"{remaining} item(s) remaining."
            )

            if remaining == 0 or updated == 0:
                break

        except Exception as error:
            print(
                f"Standardization failed: {error}"
            )
            break

    # Rebuild supplier price comparison
    try:
        comparison_count = (
            app.rebuild_price_comparison()
        )

        print(
            f"Price Comparison rebuilt with "
            f"{comparison_count} products."
        )

    except Exception as error:
        print(
            f"Price comparison rebuild failed: {error}"
        )

    print(
        f"Finished: "
        f"{processed_count} invoice(s) processed, "
        f"{failed_count} failed."
    )


if __name__ == "__main__":
    main()
