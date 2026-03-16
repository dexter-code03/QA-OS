import static com.kms.katalon.core.checkpoint.CheckpointFactory.findCheckpoint
import static com.kms.katalon.core.testcase.TestCaseFactory.findTestCase
import static com.kms.katalon.core.testdata.TestDataFactory.findTestData
import static com.kms.katalon.core.testobject.ObjectRepository.findTestObject
import static com.kms.katalon.core.testobject.ObjectRepository.findWindowsObject

import com.kms.katalon.core.model.FailureHandling
import com.kms.katalon.core.testobject.TestObject
import com.kms.katalon.core.webui.keyword.WebUiBuiltInKeywords as WebUI
import com.kms.katalon.core.mobile.keyword.MobileBuiltInKeywords as Mobile
import com.kms.katalon.core.cucumber.keyword.CucumberBuiltinKeywords as CucumberKW
import com.kms.katalon.core.windows.keyword.WindowsBuiltinKeywords as Windows
import internal.GlobalVariable

import ObjectRepository_Figma as OR

/**
 * Test Case: TC_AddressVerification_Figma
 * Description: Verifies the Melissa address verification flow, including
 *              AV23+ pass, below AV23 error, and retry flow with confirmation.
 *              Uses Figma component names as object locators.
 */
@com.kms.katalon.core.annotation.Keyword
class TC_AddressVerification_Figma {

    /**
     * Helper method to simulate typing with a debounce delay.
     * @param to The TestObject for the input field.
     * @param text The text to type.
     * @param debounceDelayMs The delay in milliseconds to simulate debounce.
     */
    @com.kms.katalon.core.annotation.Keyword
    def typeWithDebounce(TestObject to, String text, int debounceDelayMs) {
        Mobile.setText(to, text, 0)
        Mobile.delay(debounceDelayMs / 1000)
        println "Typed '${text}' into '${to.getObjectId()}' and waited for debounce."
    }

    /**
     * Helper method to perform a full address search and selection.
     * @param addressToSearch The address string to type.
     * @param expectedSuggestion The expected suggestion to select.
     */
    @com.kms.katalon.core.annotation.Keyword
    def performAddressSearchAndSelect(String addressToSearch, String expectedSuggestion) {
        Mobile.waitForElementPresent(OR.AddressSearchInput_Figma, 10, FailureHandling.STOP_ON_FAILURE)
        println "Starting address search for: '${addressToSearch}'"
        typeWithDebounce(OR.AddressSearchInput_Figma, addressToSearch, 2000)
        Mobile.waitForElementPresent(OR.AnySuggestionItem_Figma, 10, FailureHandling.STOP_ON_FAILURE)
        Mobile.verifyElementPresent(OR.SuggestionItem_Figma(expectedSuggestion), 5, FailureHandling.STOP_ON_FAILURE)
        Mobile.tap(OR.SuggestionItem_Figma(expectedSuggestion), 5)
        println "Selected suggestion: '${expectedSuggestion}'"
        Mobile.waitForElementPresent(OR.StreetNameInput_Figma, 10, FailureHandling.STOP_ON_FAILURE)
        println "Address fields auto-mapped."
    }

    /**
     * Main test method for address verification.
     */
    @com.kms.katalon.core.annotation.Keyword
    def execute() {
        // --- Pre-conditions: Assume user is on the address entry screen ---
        Mobile.startApplication(GlobalVariable.G_AppPath, false)
        Mobile.waitForElementPresent(OR.HelloNaveenText_Figma, 30, FailureHandling.STOP_ON_FAILURE)
        println "Navigated to initial screen."

        if (Mobile.waitForElementPresent(OR.GenericContinueButton_Figma, 5, FailureHandling.OPTIONAL)) {
            Mobile.tap(OR.GenericContinueButton_Figma, 5)
            println "Tapped generic continue button to proceed to address entry."
        } else {
            println "Generic continue button not found, assuming direct navigation to address entry screen."
        }

        // --- Scenario 1: AV23+ Pass ---
        println "\n--- Scenario 1: AV23+ Pass ---"
        performAddressSearchAndSelect("1600 Amphitheatre Parkway", "1600 Amphitheatre Pkwy, Mountain View, CA 94043, USA")
        Mobile.tap(OR.ContinueButton_Figma, 5)
        println "Tapped 'Continue' button for AV23+ scenario."
        // Simulate Melissa API response with AV23+ (e.g., by checking for next screen)
        Mobile.waitForElementPresent(OR.NextOnboardingStepScreen_Figma, 10, FailureHandling.STOP_ON_FAILURE)
        println "Verified user proceeded to the next onboarding step (AV23+ pass)."
        Mobile.pressBack() // Go back to simulate starting fresh for next scenario
        Mobile.waitForElementPresent(OR.AddressSearchInput_Figma, 10, FailureHandling.STOP_ON_FAILURE)


        // --- Scenario 2: Below AV23 Error ---
        println "\n--- Scenario 2: Below AV23 Error ---"
        performAddressSearchAndSelect("123 Fake Street", "123 Fake St, Anytown, USA") // Address likely to fail verification
        Mobile.tap(OR.ContinueButton_Figma, 5)
        println "Tapped 'Continue' button for below AV23 scenario."
        // FRD: "The user is shown a friendly error message: 'The address you entered could not be verified. Please try again.'"
        Mobile.waitForElementPresent(OR.AddressVerificationErrorMessage_Figma, 10, FailureHandling.STOP_ON_FAILURE)
        Mobile.verifyElementText(OR.AddressVerificationErrorMessage_Figma, "The address you entered could not be verified. Please try again.", FailureHandling.STOP_ON_FAILURE)
        Mobile.verifyElementPresent(OR.TryAgainButton_Figma, 5, FailureHandling.STOP_ON_FAILURE)
        println "Verified error message and 'Try Again' button are displayed."

        // FRD: "The user is returned to the address entry/edit screen."
        Mobile.tap(OR.TryAgainButton_Figma, 5)
        Mobile.waitForElementPresent(OR.AddressSearchInput_Figma, 10, FailureHandling.STOP_ON_FAILURE)
        println "Verified user returned to address entry/edit screen after tapping 'Try Again'."


        // --- Scenario 3: Retry Flow with Confirmation Screen ---
        println "\n--- Scenario 3: Retry Flow with Confirmation Screen ---"
        // Start with an address that will initially fail verification
        performAddressSearchAndSelect("456 Invalid Rd", "456 Invalid Rd, Someplace, USA")
        Mobile.tap(OR.ContinueButton_Figma, 5)
        println "Tapped 'Continue' button for retry flow (initial failure)."
        Mobile.waitForElementPresent(OR.AddressVerificationErrorMessage_Figma, 10, FailureHandling.STOP_ON_FAILURE)
        Mobile.tap(OR.TryAgainButton_Figma, 5)
        Mobile.waitForElementPresent(OR.StreetNameInput_Figma, 10, FailureHandling.STOP_ON_FAILURE)
        println "Returned to edit screen after initial failure."

        // FRD: "If the user edits one or more allowed fields and the updated address is not found in Melissa records or does not meet the verification threshold:"
        // Edit an allowed field (e.g., add a unit number)
        String userEditedUnit = "Unit B"
        Mobile.setText(OR.UnitApartmentInput_Figma, userEditedUnit, 0)
        println "Edited 'Unit / Apartment' field to: '${userEditedUnit}'"
        Mobile.tap(OR.ContinueButton_Figma, 5)
        println "Tapped 'Continue' button after editing."

        // FRD: "A confirmation screen is shown to the user."
        Mobile.waitForElementPresent(OR.ConfirmationScreenTitle_Figma, 10, FailureHandling.STOP_ON_FAILURE)
        Mobile.verifyElementText(OR.ConfirmationScreenTitle_Figma, "Address Confirmation", FailureHandling.STOP_ON_FAILURE) // Assuming a title
        Mobile.verifyElementPresent(OR.UserEnteredAddressText_Figma, 5, FailureHandling.STOP_ON_FAILURE)
        Mobile.verifyElementPresent(OR.MelissaRecommendedAddressText_Figma, 5, FailureHandling.STOP_ON_FAILURE)
        Mobile.verifyElementPresent(OR.ProceedWithUserAddressButton_Figma, 5, FailureHandling.STOP_ON_FAILURE)
        Mobile.verifyElementPresent(OR.ProceedWithMelissaAddressButton_Figma, 5, FailureHandling.STOP_ON_FAILURE)
        Mobile.verifyElementPresent(OR.GoBackToEditAddressButton_Figma, 5, FailureHandling.STOP_ON_FAILURE)
        println "Verified confirmation screen elements are displayed."

        // FRD: "The user can choose either option and continue or go back to edit the address again."
        // Option A: Proceed with user-entered address
        Mobile.tap(OR.ProceedWithUserAddressButton_Figma, 5)
        println "Tapped 'Proceed with user address' button."
        Mobile.waitForElementPresent(OR.NextOnboardingStepScreen_Figma, 10, FailureHandling.STOP_ON_FAILURE)
        println "Verified user proceeded to the next onboarding step (after choosing user address)."
        Mobile.pressBack() // Go back to simulate starting fresh for next scenario
        Mobile.waitForElementPresent(OR.AddressSearchInput_Figma, 10, FailureHandling.STOP_ON_FAILURE)

        // Option B: Proceed with Melissa recommended address (re-run scenario 3 up to confirmation screen)
        println "\n--- Scenario 3 (Cont.): Proceed with Melissa Recommended Address ---"
        performAddressSearchAndSelect("456 Invalid Rd", "456 Invalid Rd, Someplace, USA")
        Mobile.tap(OR.ContinueButton_Figma, 5)
        Mobile.waitForElementPresent(OR.AddressVerificationErrorMessage_Figma, 10, FailureHandling.STOP_ON_FAILURE)
        Mobile.tap(OR.TryAgainButton_Figma, 5)
        Mobile.waitForElementPresent(OR.StreetNameInput_Figma, 10, FailureHandling.STOP_ON_FAILURE)
        Mobile.setText(OR.UnitApartmentInput_Figma, userEditedUnit, 0)
        Mobile.tap(OR.ContinueButton_Figma, 5)
        Mobile.waitForElementPresent(OR.ConfirmationScreenTitle_Figma, 10, FailureHandling.STOP_ON_FAILURE)
        println "Reached confirmation screen again."

        Mobile.tap(OR.ProceedWithMelissaAddressButton_Figma, 5)
        println "Tapped 'Proceed with Melissa recommended address' button."
        Mobile.waitForElementPresent(OR.NextOnboardingStepScreen_Figma, 10, FailureHandling.STOP_ON_FAILURE)
        println "Verified user proceeded to the next onboarding step (after choosing Melissa address)."

        println "\nAll address verification scenarios completed successfully."
    }
}