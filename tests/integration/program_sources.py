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

#ifdef __linux__
#include <sys/prctl.h>

#ifndef PR_SET_PTRACER
#define PR_SET_PTRACER 0x59616d61
#endif

#ifndef PR_SET_PTRACER_ANY
#define PR_SET_PTRACER_ANY ((unsigned long)-1)
#endif
#endif

int main(void) {
#ifdef __linux__
    /* Kernels run with yama ptrace_scope=1 by default, which only lets a
       process be traced by its own descendants. The debugger attaches as a
       sibling, so the target has to authorize any tracer explicitly. */
    prctl(PR_SET_PTRACER, PR_SET_PTRACER_ANY);
#endif

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
