"""
Sample code with intentional issues for testing the code review agent.
Run: python code_reviewer.py sample_code.py
"""

import os
import sys
import json  # unused import

API_KEY = "sk-secret-12345-abcdef"  # Hardcoded secret!

def process_data(data, flag1, flag2, flag3, option1, option2, callback):  # Too many parameters
    results = []
    for item in data:
        for subitem in item:  # Nested loop
            for element in subitem:  # Double nested!
                if element > 0:
                    results.append(element * 2)
    return results


def calculate_something(x, y):
    # Missing docstring, missing type hints
    result = x * 3.14159  # Magic number
    if y > 10:
        return result
    elif y > 5:
        return result / 2
    elif y > 0:
        return result / 4
    else:
        return 0


class DataProcessor:
    def __init__(self):
        self.data = []
    
    def load(self, path):
        # This line is intentionally very long to trigger the line length check - it keeps going and going beyond reasonable limits
        with open(path) as f:
            self.data = f.read()
    
    def query(self, user_input):
        # SQL injection vulnerability
        query = f"SELECT * FROM users WHERE name = '{user_input}'"
        return query


password = "admin123"  # Another hardcoded secret

def good_function(items: list[int]) -> int:
    """
    Calculate the sum of all items.
    
    Args:
        items: A list of integers to sum.
        
    Returns:
        The sum of all items in the list.
    """
    return sum(items)


# TODO: Refactor this function
# FIXME: This doesn't handle edge cases
def problematic_function(data):
    x = 1
    y = 2  # unused variable
    return data
