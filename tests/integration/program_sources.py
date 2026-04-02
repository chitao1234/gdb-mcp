"""Shared program sources for direct-v2 integration tests."""

TEST_CPP_PROGRAM = """
#include <iostream>

int add(int a, int b) {
    int result = a + b;
    return result;
}

int multiply(int x, int y) {
    int product = x * y;
    return product;
}

int calculate(int num) {
    int sum = add(num, 10);
    int prod = multiply(sum, 2);
    return prod;
}

int main() {
    int value = 5;
    int result = calculate(value);
    std::cout << "Result: " << result << std::endl;
    return 0;
}
"""

CRASHING_C_PROGRAM = """
#include <signal.h>

int main(void) {
    raise(SIGABRT);
    return 0;
}
"""

WATCH_MEMORY_C_PROGRAM = """
int watched = 0x12345678;

int main(void) {
    watched = 0x12345679;
    return watched;
}
"""

FORKING_C_PROGRAM = """
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

int main(void) {
    pid_t pid = fork();
    if (pid == 0) {
        _exit(0);
    }
    waitpid(pid, 0, 0);
    return 0;
}
"""

DELAY_EXIT_C_PROGRAM = """
#include <unistd.h>

int main(void) {
    sleep(3);
    return 0;
}
"""

ATTACHABLE_C_PROGRAM = """
#include <unistd.h>

int main(void) {
    while (1) {
        sleep(1);
    }
    return 0;
}
"""

TEST_PROGRAM_1 = """
#include <stdio.h>

int double_value(int x) {
    return x * 2;
}

int main() {
    int num = 10;
    int result = double_value(num);
    printf("Result: %d\\n", result);
    return 0;
}
"""

TEST_PROGRAM_2 = """
#include <stdio.h>

int triple_value(int x) {
    return x * 3;
}

int main() {
    int num = 7;
    int result = triple_value(num);
    printf("Result: %d\\n", result);
    return 0;
}
"""
