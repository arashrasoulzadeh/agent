import os

def delete(path):
    try:
        if os.path.isdir(path):
            os.rmdir(path)
            print(f'Deleted directory: {path}')
        elif os.path.isfile(path):
            os.remove(path)
            print(f'Deleted file: {path}')
        else:
            print(f'No such file or directory: {path}')
    except Exception as e:
        print(f'Error deleting {path}: {e}')