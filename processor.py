import app


def main():
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
                f"FAILED: "
                f"{selected_file['name']} "
                f"— {error}"
            )

    if processed_count > 0:
        print(
            "Rebuilding price comparison..."
        )

        count = (
            app.rebuild_price_comparison()
        )

        print(
            f"Price Comparison now has "
            f"{count} standardized products."
        )

    print(
        f"Finished. "
        f"{processed_count} processed, "
        f"{failed_count} failed."
    )


if __name__ == "__main__":
    main()
