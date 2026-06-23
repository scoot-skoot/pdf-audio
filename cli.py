# User-facing CLI entrypoint. Thin wrapper over app.pipeline.run_pipeline.
import sys
import argparse

from app.pipeline import run_pipeline


def main(argv):
    parser = argparse.ArgumentParser(description="Convert a PDF into an MP3 audiobook.")
    parser.add_argument("pdf_path", help="Path to the input PDF")
    # No choices=: an invalid value falls back to automatic detection per design.
    parser.add_argument("--mode", default=None, help="Pipeline mode: structured | narrative")
    parser.add_argument(
        "--trim-matter",
        action="store_true",
        help="Remove detected front/back matter (LLM); narrate main content only.",
    )
    args = parser.parse_args(argv)
    run_pipeline(args.pdf_path, args.mode, args.trim_matter)


if __name__ == "__main__":
    main(sys.argv[1:])
