import os

def create_directory(directory_path: str) -> None:
    """Creates a new directory if it does not exist."""
    try:
        os.makedirs(directory_path, exist_ok=True)
        print(f"Directory '{directory_path}' created successfully.")
    except Exception as e:
        print(f"Error creating directory '{directory_path}': {e}")

if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print('Usage: python create_directory.py <directory_path>')
        sys.exit(1)
    directory = sys.argv[1]
    create_directory(directory)