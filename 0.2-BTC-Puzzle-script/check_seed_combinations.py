# Updated Code for check_seed_combinations.py

# Adding calculation of total combinations and progress percentage display

def calculate_combinations(n):
    total_combinations = 2 ** n  # Assuming binary combinations for n bits
    return total_combinations


def display_progress(current, total):
    percentage = (current / total) * 100
    print(f'Progress: {percentage:.2f}%')

# Example usage
if __name__ == '__main__':
    n = 10  # Example value
    total = calculate_combinations(n)
    for current in range(total + 1):
        display_progress(current, total)