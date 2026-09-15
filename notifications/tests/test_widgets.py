from notifications.widgets import TrixEditorWidget


def test_trix_editor_widget_renders_a_hidden_input_bound_to_a_trix_editor():
    widget = TrixEditorWidget()

    html = widget.render("text", None, attrs={"id": "id_text"})

    assert '<input type="hidden" id="id_text" name="text" value="">' in html
    assert '<trix-editor input="id_text">' in html


def test_trix_editor_widget_prefills_the_hidden_input_with_the_initial_value():
    widget = TrixEditorWidget()

    html = widget.render("text", "<div>Mazel tov!</div>", attrs={"id": "id_text"})

    assert 'value="&lt;div&gt;Mazel tov!&lt;/div&gt;"' in html


def test_trix_editor_widget_falls_back_to_a_derived_id_when_none_is_given():
    widget = TrixEditorWidget()

    html = widget.render("text", None, attrs=None)

    assert 'id="id_text"' in html
    assert 'input="id_text"' in html
