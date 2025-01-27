""" Tests for creating and updating command templates """
import httpie.cli.argtemplate
import tempfile
import json
import os


class TestStoreTemplate:

    def test_store_normal_template_with_method(self):
        """
        Tests that a valid template can be stored properly when the template contains a method parameter
        """

        with tempfile.NamedTemporaryFile('w+', delete=False) as temp_fp:

            httpie.cli.argtemplate.TEMPLATE_FILE = temp_fp.name

            command = 'http template test_template GET https://catfact.ninja/fact param1=value1 param2=value2'
            args = command.split()

            httpie.cli.argtemplate.store_json_template(args[2:])

            stored_templates = json.load(temp_fp)
            assert stored_templates[args[2]] is not None

            template = stored_templates[args[2]]
            assert template['method'] == args[3]
            assert template['url'] == args[4]
            assert template['data'] is not None

            template_data = template['data']
            assert template_data['param1'] == 'value1'
            assert template_data['param2'] == 'value2'

    def test_store_template_overwrites_old_template_same_name(self):
        """
        Tests that storing a template with the same name as an already existing template will overwrite the existing template
        """
        with tempfile.NamedTemporaryFile('w+', delete=False) as temp_fp:

            httpie.cli.argtemplate.TEMPLATE_FILE = temp_fp.name

            command = 'http template test_template GET https://catfact.ninja/fact param1=value1 param2=value2'
            args = command.split()

            httpie.cli.argtemplate.store_json_template(args[2:])

            old_stored_templates = json.load(temp_fp)

            command = 'http template test_template POST https://catfact.ninja/fact param1=value2 param2=value1'
            args = command.split()
            httpie.cli.argtemplate.store_json_template(args[2:])

            temp_fp.seek(0)
            new_stored_templates = json.load(temp_fp)

            assert len(old_stored_templates) == len(new_stored_templates)
            assert new_stored_templates[args[2]]['method'] == 'POST'
            assert new_stored_templates[args[2]]['data']['param1'] == 'value2'
            assert new_stored_templates[args[2]]['data']['param2'] == 'value1'

    def test_store_template_invalid_method(self):
        """
        Tests that storing a template with an invalid method argument leads to the template being saved without a method parameter
        """
        with tempfile.NamedTemporaryFile('w+', delete=False) as temp_fp:
            httpie.cli.argtemplate.TEMPLATE_FILE = temp_fp.name

            command = 'http template test_template NOT_VALID_METHOD https://catfact.ninja/fact param1=value1 param2=value2'
            args = command.split()

            httpie.cli.argtemplate.store_json_template(args[2:])

            stored_templates = json.load(temp_fp)
            template = stored_templates[args[2]]
            assert template is not None
            assert template['url'] == args[4]
            assert template['method'] is None
            assert template['data']['param1'] == 'value1'
            assert template['data']['param2'] == 'value2'

    def test_store_template_no_templates_json(self):
        """
        Tests that a new file will be created for storing templates if templates.json (TEMPLATE_FILE) doesn't exist
        """
        httpie.cli.argtemplate.TEMPLATE_FILE = "NOT_A_REAL_FILE"
        assert not os.path.isfile(httpie.cli.argtemplate.TEMPLATE_FILE)

        httpie.cli.argtemplate.store_json_template(["fake_template", "GET", "https://catfact.ninja/fact"])
        assert os.path.isfile(httpie.cli.argtemplate.TEMPLATE_FILE)

        fp = open(httpie.cli.argtemplate.TEMPLATE_FILE, "r")
        templates = json.load(fp)

        assert templates["fake_template"]["method"] == "GET"
        assert templates["fake_template"]["url"] == "https://catfact.ninja/fact"

        os.remove(httpie.cli.argtemplate.TEMPLATE_FILE)


class TestEditTemplate:
    def test_edit_template_update_value(self):
        """
        Tests that the edit_json_template function can correctly update a value
        """
        with tempfile.NamedTemporaryFile('w+', delete=False) as temp_fp:
            httpie.cli.argtemplate.TEMPLATE_FILE = temp_fp.name

            command = 'http template test_template GET https://catfact.ninja/fact param1=value1 param2=value2'
            args = command.split()

            httpie.cli.argtemplate.store_json_template(args[2:])

            httpie.cli.argtemplate.edit_json_template(['test_template', 'param1', 'newvalue1'])

            stored_templates = json.load(temp_fp)
            assert stored_templates[args[2]] is not None

            template = stored_templates[args[2]]
            assert template['method'] == 'GET'
            assert template['url'] == args[4]
            assert template['data'] is not None

            template_data = template['data']
            assert template_data['param1'] == 'newvalue1'
            assert template_data['param2'] == 'value2'

    def test_edit_template_change_url(self):
        """
        Tests that you can change the URL of a template
        """
        with tempfile.NamedTemporaryFile('w+', delete=False) as temp_fp:
            httpie.cli.argtemplate.TEMPLATE_FILE = temp_fp.name

            command = 'http template test_template GET https://catfact.ninja/fact param1=value1 param2=value2'
            args = command.split()

            httpie.cli.argtemplate.store_json_template(args[2:])

            httpie.cli.argtemplate.edit_json_template(['test_template', 'url', 'https://fake-url/'])

            stored_templates = json.load(temp_fp)
            assert len(stored_templates) == 1
            assert stored_templates[args[2]] is not None

            template = stored_templates[args[2]]
            assert template['method'] == 'GET'
            assert template['url'] == 'https://fake-url/'
            assert template['data'] is not None

    def test_edit_template_change_method(self):
        """
        Tests that you can change the method of a template
        """
        with tempfile.NamedTemporaryFile('w+', delete=False) as temp_fp:
            httpie.cli.argtemplate.TEMPLATE_FILE = temp_fp.name

            command = 'http template test_template GET https://catfact.ninja/fact param1=value1 param2=value2'
            args = command.split()

            httpie.cli.argtemplate.store_json_template(args[2:])

            httpie.cli.argtemplate.edit_json_template(['test_template', 'method', 'POST'])

            stored_templates = json.load(temp_fp)
            assert len(stored_templates) == 1
            assert stored_templates[args[2]] is not None

            template = stored_templates[args[2]]
            assert template['method'] == 'POST'
            assert template['url'] == 'https://catfact.ninja/fact'
            assert template['data'] is not None

    def test_edit_template_not_found(self, capsys):
        """
        Tests that an exception is raised when trying to edit a template that doesn't exist
        """
        with tempfile.NamedTemporaryFile('w+', delete=False) as temp_fp:
            httpie.cli.argtemplate.TEMPLATE_FILE = temp_fp.name

            httpie.cli.argtemplate.edit_json_template(['test_template', 'param', 'value'])
            out, _ = capsys.readouterr()
            assert "Template 'test_template' does not exist." in out

    def test_edit_template_key_not_found(self):
        """
        Tests that you can add new key-value pairs to an existing template
        """
        with tempfile.NamedTemporaryFile('w+', delete=False) as temp_fp:
            httpie.cli.argtemplate.TEMPLATE_FILE = temp_fp.name

            command = 'http template test_template GET https://catfact.ninja/fact param1=value1'
            args = command.split()

            httpie.cli.argtemplate.store_json_template(args[2:])
            old_stored_templates = json.load(temp_fp)
            assert 'param2' not in old_stored_templates[args[2]]['data']

            temp_fp.seek(0)
            httpie.cli.argtemplate.edit_json_template(['test_template', 'param2', 'value2'])
            new_stored_templates = json.load(temp_fp)
            assert new_stored_templates[args[2]]['data']['param2'] == 'value2'

    def test_edit_template_no_templates_json(self):
        """
        Tests that a new file will be created for editing templates if templates.json (TEMPLATE_FILE) doesn't exist
        """
        httpie.cli.argtemplate.TEMPLATE_FILE = "NOT_A_REAL_FILE"
        assert not os.path.isfile(httpie.cli.argtemplate.TEMPLATE_FILE)

        httpie.cli.argtemplate.edit_json_template(["fake_template", "param2", "value2"])
        assert os.path.isfile(httpie.cli.argtemplate.TEMPLATE_FILE)

        os.remove(httpie.cli.argtemplate.TEMPLATE_FILE)


class TestLoadTemplate:
    def test_load_template(self):
        """
        Tests that loading a template yields the same arguments that were passed (except for 'http template <name>')
        """
        with tempfile.NamedTemporaryFile('w+', delete=False) as temp_fp:
            httpie.cli.argtemplate.TEMPLATE_FILE = temp_fp.name

            command = 'http template test_template GET https://catfact.ninja/fact param1=value1 param2=value2'
            args = command.split()

            httpie.cli.argtemplate.store_json_template(args[2:])
            loaded_args = httpie.cli.argtemplate.load_template(args[2])

            for i in range(len(args) - 3):
                assert args[i + 3] == loaded_args[i]

    def test_load_template_not_found(self, capsys):
        """
        Tests loading a template when the name of the template to load cannot be found in the template file
        """
        with tempfile.NamedTemporaryFile('w+', delete=False) as temp_fp:
            httpie.cli.argtemplate.TEMPLATE_FILE = temp_fp.name

            command = 'http template test_template GET https://catfact.ninja/fact param1=value1 param2=value2'
            args = command.split()

            loaded_args = httpie.cli.argtemplate.load_template(args[2])
            out, _ = capsys.readouterr()
            assert loaded_args == []
            assert "Template 'test_template' does not exist." in out

    def test_load_template_no_templates_json(self):
        """
        Tests that a new file will be created for loading templates if templates.json (TEMPLATE_FILE) doesn't exist
        """
        httpie.cli.argtemplate.TEMPLATE_FILE = "NOT_A_REAL_FILE"
        assert not os.path.isfile(httpie.cli.argtemplate.TEMPLATE_FILE)

        httpie.cli.argtemplate.load_template("fake_template")
        assert os.path.isfile(httpie.cli.argtemplate.TEMPLATE_FILE)

        os.remove(httpie.cli.argtemplate.TEMPLATE_FILE)


class TestDeleteTemplate:
    def test_delete_template(self):
        """
        Tests that a template being deleted doesn't delete other templates
        """
        with tempfile.NamedTemporaryFile('w+', delete=False) as temp_fp:
            httpie.cli.argtemplate.TEMPLATE_FILE = temp_fp.name

            command = 'http template test_template GET https://catfact.ninja/fact param1=value1 param2'
            args = command.split()

            httpie.cli.argtemplate.store_json_template(args[2:])
            command2 = 'http template other_template GET https://catfact.ninja/fact param1=value1'
            args2 = command2.split()

            httpie.cli.argtemplate.delete_template(args2[2])

            fp = open(httpie.cli.argtemplate.TEMPLATE_FILE, "r")
            stored_templates = json.load(fp)
            assert "test_template" in stored_templates
            assert "other_template" not in stored_templates
            assert len(stored_templates) == 1
            assert stored_templates[args[2]]["method"] == "GET"
            assert stored_templates[args[2]]["url"] == "https://catfact.ninja/fact"
            assert stored_templates[args[2]]["data"]["param1"] == "value1"
            assert "param2" not in stored_templates[args[2]]["data"]

    def test_delete_only_one_template(self):
        """
        Tests that a template can be deleted when it's the only template in templates.json
        """
        with tempfile.NamedTemporaryFile('w+', delete=False) as temp_fp:
            httpie.cli.argtemplate.TEMPLATE_FILE = temp_fp.name

            command = 'http template test_template GET https://catfact.ninja/fact param1=value1'
            args = command.split()

            httpie.cli.argtemplate.store_json_template(args[2:])

            httpie.cli.argtemplate.delete_template(args[2])

            fp = open(httpie.cli.argtemplate.TEMPLATE_FILE, "r")
            stored_templates = json.load(fp)
            assert stored_templates == {}

    def test_delete_template_no_templates_json(self):
        """
        Tests that a new file will be created for deleting templates if templates.json (TEMPLATE_FILE) doesn't exist
        """
        httpie.cli.argtemplate.TEMPLATE_FILE = "NOT_A_REAL_FILE"
        assert not os.path.isfile(httpie.cli.argtemplate.TEMPLATE_FILE)

        httpie.cli.argtemplate.delete_template("fake_template")
        assert os.path.isfile(httpie.cli.argtemplate.TEMPLATE_FILE)

        os.remove(httpie.cli.argtemplate.TEMPLATE_FILE)

    def test_delete_template_doesnt_exist(self, capsys):
        """
        Tests that an error message is printed when trying to delete a template that doesn't exist
        """
        with tempfile.NamedTemporaryFile('w+', delete=False) as temp_fp:
            httpie.cli.argtemplate.TEMPLATE_FILE = temp_fp.name

            command = 'http template test_template GET https://catfact.ninja/fact param1=value1 param2=value2'
            args = command.split()
            httpie.cli.argtemplate.store_json_template(args[2:])
            httpie.cli.argtemplate.delete_template("asdf")
            out, _ = capsys.readouterr()
            assert "Template 'asdf' does not exist." in out
