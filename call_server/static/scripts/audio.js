/*global CallPower, Backbone */

(function () {
  CallPower.Views.CampaignAudioForm = Backbone.View.extend({
    el: $('form#audio'),

    events: {
      'click .record': 'onRecord',
      'click .play': 'onPlay',
      'click .upload': 'onUpload',
      'click .version': 'onVersion',

      'submit': 'submitForm'
    },

    initialize: function() {
  
    },

    onRecord: function(event) {
      event.preventDefault();

      // pull modal info from related fields
      var inputGroup = $(event.target).parents('.input-group');
      var modal = { name: inputGroup.prev('label').text(),
                    description: inputGroup.find('.description .help-inline').text(),
                    example_text: inputGroup.find('.description .example-text').text()
                  };
      this.microphoneView = new CallPower.Views.MicrophoneModal();
      this.microphoneView.render(modal);
    },

    onPlay: function(event) {
      event.preventDefault();
    },

    onVersion: function(event) {
      event.preventDefault();
    },

    validateForm: function() {
      var isValid = true;

      // call validators
      
      return isValid;
    },

    submitForm: function(event) {
      event.preventDefault();

      if (this.validateForm()) {
        $(this.$el).unbind('submit').submit();
        return true;
      }
      return false;
    }

  });

})();