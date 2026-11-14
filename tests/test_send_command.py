import tempfile
import unittest
from unittest.mock import AsyncMock

from localthings.server import Device, Subscription
from fixtures import configuration, registration
from test_server import Connection, status, token
from thingslocal.controls import ControlRejected
from thingslocal.server import ApplianceServer


class SendCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_command_payload_is_exact_and_bound_to_the_session(self):
        config = configuration(); selected = config['devices'][1]
        with tempfile.TemporaryDirectory() as directory:
            server = ApplianceServer(config, directory, None, None, status(config), lambda: None)
            record = registration(selected); server.registerDevice(Device.fromDict(record))
            connection = Connection(selected['ip']); connection._logger = connection._logger.bind(connid=11)
            connection._client = server._clientFactory(connection, record['id'], token(selected))
            connection._closeConnection = False
            connection._subscriptions = {Subscription('/v1/commands/' + record['id']): 1}
            connection.publish = AsyncMock(); server._connections.add(connection)
            await server.send_command(selected['id'], 11, 'main', 'samsungce.dishwasherWashingOptions',
                                      'setSanitize', [True])
            args, kwargs = connection.publish.await_args
            self.assertEqual(str(args[0]), '/v1/commands/' + record['id']); self.assertEqual(kwargs, {'qos': 1})
            commands = args[1]['commands']; self.assertEqual(len(commands), 1)
            command = dict(commands[0]); self.assertEqual(len(command.pop('id')), 36)
            self.assertEqual(command, {'component': 'main', 'capability': 'samsungce.dishwasherWashingOptions',
                                       'command': 'setSanitize', 'arguments': [True]})
            with self.assertRaises(ControlRejected):
                await server.send_command(selected['id'], 10, 'main', 'switch', 'off', [])
            with self.assertRaises(ControlRejected):
                await server.send_command(config['devices'][0]['id'], 11, 'main', 'switch', 'off', [])
            connection.publish.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
