from locust import HttpUser, task, between

class AsianOptionsUser(HttpUser):
    wait_time = between(0.1, 0.5)

    @task
    def pricing(self):
        self.client.get("/pricing")
