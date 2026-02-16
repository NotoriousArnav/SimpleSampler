import sys
import os

# Add src to path so imports work
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from simplesampler.tui.app import Application


def main(*args, **kwargs) -> None:
    # Check if a bank file is provided as an argument
    bank_file = None
    if len(sys.argv) > 1:
        bank_file = sys.argv[1]

    if not bank_file:
        raise Exception("No bank file provided. Please provide a bank file as an argument.")
        sys.exit(1)

    app = Application(bank_path=bank_file)
    app.run()


if __name__ == "__main__":
    main()
