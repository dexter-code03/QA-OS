import com.kms.katalon.core.testobject.TestObject
import com.kms.katalon.core.testobject.MobileTestObject
import com.kms.katalon.core.testobject.ConditionType

public class ObjectRepository_Figma {

    /**
     * Helper method to create a MobileTestObject with a given name and accessibility ID.
     * This assumes accessibility ID is the primary locator strategy for Figma components.
     *
     * @param name The name of the TestObject (e.g., "AddressSearchInput_Figma").
     * @param accessibilityId The accessibility ID of the element (derived from Figma component name or FRD description).
     * @return A configured MobileTestObject.
     */
    static TestObject makeTO(String name, String accessibilityId) {
        MobileTestObject mto = new MobileTestObject(name)
        mto.addProperty("accessibility", ConditionType.EQUALS, accessibilityId)
        return mto
    }

    /**
     * Helper method to create a MobileTestObject with a given name and XPath.
     * Used when accessibility ID is not suitable or for dynamic elements.
     *
     * @param name The name of the TestObject (e.g., "SuggestionItem_Figma").
     * @param xpath The XPath of the element.
     * @return A configured MobileTestObject.
     */
    static TestObject makeTO_XPath(String name, String xpath) {
        MobileTestObject mto = new MobileTestObject(name)
        mto.addProperty("xpath", ConditionType.EQUALS, xpath)
        return mto
    }

    // --- Address Search Screen Components ---

    // Figma: "single address search input field" from FRD
    // Assuming a generic input field for address search.
    static TestObject AddressSearchInput_Figma = makeTO("AddressSearchInput_Figma", "address_search_input")

    // Figma: "Suggestions are displayed in a dropdown below the input field." from FRD
    // This will be a dynamic XPath as suggestions appear.
    // Example: //*[@resource-id='com.example.app:id/suggestion_item_text' and @text='${suggestionText}']
    // For a generic item, we might use a common parent and then child text.
    static TestObject SuggestionItem_Figma(String suggestionText) {
        return makeTO_XPath("SuggestionItem_Figma_${suggestionText.replaceAll(' ', '_')}",
                "//*[@resource-id='com.example.app:id/suggestion_item_text' and @text='${suggestionText}']")
    }
    // A more generic locator for any suggestion item, useful for checking visibility of the list
    static TestObject AnySuggestionItem_Figma = makeTO_XPath("AnySuggestionItem_Figma",
            "//*[@resource-id='com.example.app:id/suggestion_item_text']")


    // --- Address Mapping/Edit Screen Components ---

    // Figma: "Continue" button from FRD
    // Assuming a generic button with text "Continue"
    static TestObject ContinueButton_Figma = makeTO("ContinueButton_Figma", "continue_button")

    // Figma: "Street Name" input field from FRD
    static TestObject StreetNameInput_Figma = makeTO("StreetNameInput_Figma", "street_name_input")

    // Figma: "House / Building Number" input field from FRD
    static TestObject HouseBuildingNumberInput_Figma = makeTO("HouseBuildingNumberInput_Figma", "house_building_number_input")

    // Figma: "Unit / Apartment" input field from FRD
    static TestObject UnitApartmentInput_Figma = makeTO("UnitApartmentInput_Figma", "unit_apartment_input")

    // Figma: "Neighborhood" input field from FRD
    static TestObject NeighborhoodInput_Figma = makeTO("NeighborhoodInput_Figma", "neighborhood_input")

    // Figma: "City" input field from FRD
    static TestObject CityInput_Figma = makeTO("CityInput_Figma", "city_input")

    // Figma: "ZIP Code" input field from FRD
    static TestObject ZipCodeInput_Figma = makeTO("ZipCodeInput_Figma", "zip_code_input")

    // Figma: "State / Territory" input field from FRD
    static TestObject StateInput_Figma = makeTO("StateInput_Figma", "state_input")

    // Figma: "Country" input field from FRD
    static TestObject CountryInput_Figma = makeTO("CountryInput_Figma", "country_input")


    // --- Address Verification Error/Confirmation Screen Components ---

    // Figma: "The address you entered could not be verified. Please try again." error message from FRD
    static TestObject AddressVerificationErrorMessage_Figma = makeTO("AddressVerificationErrorMessage_Figma", "address_verification_error_message")

    // Figma: Button to retry or go back to edit, implied by "The user is returned to the address entry/edit screen."
    static TestObject TryAgainButton_Figma = makeTO("TryAgainButton_Figma", "try_again_button")

    // Figma: Confirmation screen title from FRD (implied)
    static TestObject ConfirmationScreenTitle_Figma = makeTO("ConfirmationScreenTitle_Figma", "confirmation_screen_title")

    // Figma: "Proceed with the address entered/edited by the user." option text from FRD
    static TestObject UserEnteredAddressText_Figma = makeTO("UserEnteredAddressText_Figma", "user_entered_address_text")

    // Figma: "Select and proceed with the address recommended by Melissa." option text from FRD
    static TestObject MelissaRecommendedAddressText_Figma = makeTO("MelissaRecommendedAddressText_Figma", "melissa_recommended_address_text")

    // Figma: Button for "Proceed with user address" from FRD
    static TestObject ProceedWithUserAddressButton_Figma = makeTO("ProceedWithUserAddressButton_Figma", "proceed_user_address_button")

    // Figma: Button for "Proceed with Melissa recommended address" from FRD
    static TestObject ProceedWithMelissaAddressButton_Figma = makeTO("ProceedWithMelissaAddressButton_Figma", "proceed_melissa_address_button")

    // Figma: Button to go back to edit address again, implied by "or go back to edit the address again."
    static TestObject GoBackToEditAddressButton_Figma = makeTO("GoBackToEditAddressButton_Figma", "go_back_edit_address_button")

    // Figma: Generic "Next Onboarding Step" screen, implied after successful verification
    static TestObject NextOnboardingStepScreen_Figma = makeTO("NextOnboardingStepScreen_Figma", "next_onboarding_step_screen")

    // Figma: "Hello, Naveen 👋" text from Figma, used as a general indicator for a logged-in state or initial screen
    static TestObject HelloNaveenText_Figma = makeTO("HelloNaveenText_Figma", "hello_naveen_text") // Assuming accessibility ID for this text

    // Figma: "Button text="Continue"" from Figma, a generic continue button, could be used for initial navigation
    static TestObject GenericContinueButton_Figma = makeTO("GenericContinueButton_Figma", "generic_continue_button") // Assuming accessibility ID for this button

}