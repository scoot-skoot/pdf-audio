#include <stdio.h>
#include <unistd.h>

int main()
{
    while (1)
    {
        // Some kind of request must be recieved here
        printf("Waiting for request...\n");
        sleep(1);

    }
}