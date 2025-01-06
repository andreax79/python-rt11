import sys


def generate_lines(n):
    for i in range(0, n):
        print(f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890")


if __name__ == "__main__":
    generate_lines(int(sys.argv[1]))
