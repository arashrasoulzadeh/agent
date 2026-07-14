"""
Edit file tool.

This module provides functionality to edit files in a specified project directory.
"""

import os


def edit_file(file_path: str, new_content: str) -> None:
    """
    Edits the specified file with new content.

    :param file_path: Path to the file to be edited.
    :param new_content: New content to write to the file.
    """
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"The file {file_path} does not exist.")

    with open(file_path, 'w') as file:
        file.write(new_content)

    print(f"File {file_path} has been updated.")
