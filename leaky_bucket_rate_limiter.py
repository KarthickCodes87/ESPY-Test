import time

class LeakyBucket:
    def __init__(self, capacity, leak_rate):
        """
        Initialize the leaky bucket.
        
        :param capacity: Maximum number of requests the bucket can hold
        :param leak_rate: Number of requests that leak out per second
        """
        self.capacity = capacity
        self.leak_rate = leak_rate
        self.current_water_level = 0  # Current number of requests in the bucket
        self.last_checked = time.time()  # Time when the bucket was last updated

    def _leak(self):
        """
        Leak water (requests) from the bucket based on the elapsed time.
        """
        now = time.time()
        elapsed_time = now - self.last_checked
        leaked_water = elapsed_time * self.leak_rate
        self.current_water_level = max(0, self.current_water_level - leaked_water)
        self.last_checked = now

    def allow_request(self):
        """
        Check if a request is allowed. If allowed, add the request to the bucket.
        
        :return: True if the request is allowed, False otherwise
        """
        self._leak()
        if self.current_water_level < self.capacity:
            self.current_water_level += 1
            return True
        return False

# Example usage
if __name__ == "__main__":
    bucket = LeakyBucket(capacity=10, leak_rate=2)  # Capacity of 10 requests, leaks 2 requests per second
    
    while True:
        if bucket.allow_request():
            print("Request allowed at", time.strftime("%H:%M:%S"))
        else:
            print("Request denied at", time.strftime("%H:%M:%S"))
        time.sleep(0.5)  # Simulate requests arriving every 0.5 seconds